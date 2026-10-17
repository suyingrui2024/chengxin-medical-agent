"""Owned full-DSH workspaces. Clinical engines never receive these host tools."""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import select
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from uuid import uuid4
from urllib.parse import urlsplit

from .env_config import for_backend
from .execution_capabilities import freeze_binding, validate_receipt
from .execution_store import (
    WorkspaceStore,
    WorkspaceStoreError,
    WorkspaceOccupied,
    FORMAT,
    DSH_VERSION,
)


class ExecutionError(ValueError):
    pass


class ExecutionBusy(ExecutionError):
    pass


def route_identity(settings):
    endpoint = settings["MEDAGENT_LLM_BASE_URL"].rstrip("/")
    try:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError()
    except ValueError:
        raise ExecutionError("模型服务地址无效") from None
    return (
        hashlib.sha256(
            json.dumps(
                [endpoint, settings["MEDAGENT_LLM_MODEL"]], ensure_ascii=False
            ).encode()
        ).hexdigest(),
        parsed.hostname,
    )


def scrub(text, secret):
    return text.replace(secret, "[REDACTED]") if secret else text


def open_workspace_directory(root, identity=None):
    """Refuse missing, linked or replaced workspace roots before reading data."""
    directory = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(directory)
        if identity is not None and (info.st_dev, info.st_ino) != identity:
            raise ExecutionError("工作目录已被替换，请恢复原目录或新建工作区")
        return directory
    except (OSError, ExecutionError):
        if directory is not None:
            os.close(directory)
        raise ExecutionError("工作目录不存在、已被替换或不可读取；请恢复原目录或新建工作区") from None


def safe_file(root: Path, relative: str, limit=128_000, root_identity=None):
    """Open every component relative to an owned directory fd, never a symlink."""
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or any(p in {"..", ".", ""} for p in relative.split("/"))
        or "\\" in relative
    ):
        raise ExecutionError("文件路径无效")
    directory = open_workspace_directory(root, root_identity)
    try:
        for component in path.parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        fd = os.open(
            path.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ExecutionError("仅允许读取工作区普通文件")
            data = os.read(fd, limit + 1)
        finally:
            os.close(fd)
    except OSError:
        raise ExecutionError("文件不存在、为链接或不可读取") from None
    finally:
        os.close(directory)
    if len(data) > limit:
        raise ExecutionError("文件过大，请在本机工作目录查看")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ExecutionError("当前预览支持 UTF-8 文本文件") from None
    return {"path": relative, "text": text, "size": len(data)}


class ExecutionSession:
    def __init__(
        self,
        identifier,
        root,
        source_root,
        settings,
        reopened=False,
        store=None,
        lease=None,
        record=None,
        capability_binding=None,
    ):
        self.id = identifier
        self.directory = root / identifier
        if not reopened:
            self.directory.mkdir(parents=True, mode=0o700)
        self.state_directory = root / ".state" / identifier
        if not reopened:
            self.state_directory.mkdir(parents=True, mode=0o700)
        state_fd = open_workspace_directory(
            self.state_directory, tuple(record["state_identity"]) if record else None
        )
        state_info = os.fstat(state_fd)
        self.state_identity = (state_info.st_dev, state_info.st_ino)
        os.close(state_fd)
        # Retain the original inode until close, so deletion/recreation cannot
        # reuse its identity and silently adopt a different directory.
        self.directory_fd = open_workspace_directory(
            self.directory, tuple(record["directory_identity"]) if record else None
        )
        info = os.fstat(self.directory_fd)
        self.directory_identity = (info.st_dev, info.st_ino)
        self.storage_error = None
        self.source_root, self.settings = source_root, dict(settings)
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.status, self.error = "created", None
        self.turn_id, self.final_response = None, ""
        self.events = deque(maxlen=500)
        self.sequence = 0
        self.process = None
        self.reader_thread = None
        self.ready = threading.Event()
        self.closed = False
        self.turns = []
        self.jobs = []
        self.capability_binding = (
            record.get("capability_binding") if record else capability_binding
        )
        self.calculations = list(record.get("calculations", [])) if record else []
        self.last_inspection = 0
        self.require_resume = reopened
        self.store, self.lease = store, lease
        self.created_at = self.updated_at = time.time()
        self.title = "未命名工作区"
        self.turn_count = 0
        self.history_trimmed = False
        self.persistence_error = None
        self.restored = bool(record)
        self.execution_configured = not bool(record)
        self.configuration_error = None
        secret = settings.get("MEDAGENT_LLM_API_KEY", "")
        if not record:
            self.route_sha256, host = route_identity(settings)
            self.public_model = scrub(settings["MEDAGENT_LLM_MODEL"], secret)[:256]
            self.provider_host = scrub(host, secret)[:256]
        if record:
            self.route_sha256 = record["route_sha256"]
            self.created_at = record["created_at"]
            self.title, self.public_model, self.provider_host = (
                record["title"],
                record["model"],
                record["provider_host"],
            )
            self.require_resume = record["has_runtime_history"]
            self.turns = [dict(turn) for turn in record["turns"]]
            self.turn_count = record["turn_count"]
            self.history_trimmed = bool(record.get("history_trimmed"))
            self.status = record["status"]
            self.error = record.get("error")
            if self.status in {"starting", "running", "cancelling"}:
                self.status = "interrupted"
                self.error = "上次执行未完成。继续前请核对已有文件，避免重复操作。"
            for turn in self.turns:
                if turn["status"] in {"starting", "running", "cancelling"}:
                    turn["status"] = "interrupted"
                self.event(
                    "task.submitted",
                    turn_id=turn["id"],
                    text=turn["text"],
                    restored=True,
                )
                for result in self.calculations:
                    if result["turn_id"] == turn["id"]:
                        self.event(
                            "capability.completed",
                            turn_id=turn["id"],
                            result=result,
                            restored=True,
                        )
                if turn["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    self.event(
                        "turn." + turn["status"],
                        turn_id=turn["id"],
                        final_response=turn.get("final_response", ""),
                        restored=True,
                    )
            if self.turns:
                self.turn_id = self.turns[-1]["id"]
                self.final_response = self.turns[-1].get("final_response", "")
            self.event("workspace.restored", text="已恢复工作区和已保存的任务记录。尚未执行新任务。")
            self.bind(settings)

    def bind(self, settings):
        if self.execution_configured:
            return
        keys = ["MEDAGENT_LLM_BASE_URL", "MEDAGENT_LLM_API_KEY", "MEDAGENT_LLM_MODEL"]
        if not all(isinstance(settings.get(k), str) and settings[k] for k in keys):
            self.configuration_error = "记录已恢复。配置原工作区的模型服务和密钥后，可继续执行。"
            return
        try:
            config = for_backend(settings, "dsh")
            matches = route_identity(config)[0] == self.route_sha256
        except (ExecutionError, ValueError, TypeError):
            matches = False
        if not matches:
            self.configuration_error = "当前模型或服务与原工作区不同；可查看记录，继续执行需切回原配置。"
            return
        self.settings = dict(config)
        self.execution_configured = True
        self.configuration_error = None

    def persist(self, strict=False):
        if self.store is None or self.lease is None:
            return
        secret = self.settings.get("MEDAGENT_LLM_API_KEY", "")
        turns = []
        trimmed = self.history_trimmed or len(self.turns) > 32
        for turn in self.turns[-32:]:
            result = {
                key: turn[key]
                for key in [
                    "id",
                    "text",
                    "status",
                    "max_calls",
                    "model_calls",
                    "final_response",
                ]
                if key in turn
            }
            for key in ["text", "final_response"]:
                if key in result:
                    limit = 16000 if key == "text" else 24000
                    redacted = scrub(result[key], secret)
                    trimmed = trimmed or len(redacted) > limit
                    result[key] = redacted[:limit]
            turns.append(result)
        record = {
            "format": FORMAT,
            "dsh_version": DSH_VERSION,
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": scrub(self.title, secret)[:80],
            "model": self.public_model,
            "provider_host": self.provider_host,
            "route_sha256": self.route_sha256,
            "directory_identity": list(self.directory_identity),
            "state_identity": list(self.state_identity),
            "status": self.status,
            "has_runtime_history": self.require_resume,
            "turn_count": self.turn_count,
            "history_trimmed": trimmed,
            "turns": turns,
            "error": scrub(str(self.error), secret)[:512] if self.error else None,
            "capability_binding": self.capability_binding,
            "calculations": json.loads(
                scrub(json.dumps(self.calculations[-32:], ensure_ascii=False), secret)
            ),
        }
        while (
            len(json.dumps(record, ensure_ascii=False).encode()) > 1_000_000
            and len(record["turns"]) > 1
        ):
            record["turns"].pop(0)
            record["history_trimmed"] = True
        retained = {turn["id"] for turn in record["turns"]}
        record["calculations"] = [
            r for r in record["calculations"] if r["turn_id"] in retained
        ]
        try:
            self.store.save(self.id, record)
            self.persistence_error = None
        except (WorkspaceStoreError, OSError, ValueError):
            self.persistence_error = "工作区记录未能保存，请检查磁盘空间和目录权限；当前进程内仍可查看。"
            if strict:
                raise ExecutionError(self.persistence_error) from None

    def summary(self):
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "model": self.public_model,
            "provider_host": self.provider_host,
            "updated_at": self.updated_at,
            "directory": str(self.directory),
        }

    def event(self, kind, **payload):
        with self.lock:
            self.sequence += 1
            event = {
                "sequence": self.sequence,
                "type": kind,
                "time": time.time(),
                **payload,
            }
            self.events.append(event)
            return event

    def start_turn(self, text, max_calls=24):
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 16000
            or type(max_calls) is not int
            or not 1 <= max_calls <= 64
        ):
            raise ExecutionError("请填写任务；单轮调用上限需为 1–64")
        with self.lock:
            if self.closed:
                raise ExecutionError("工作区已关闭")
            if not self.execution_configured:
                raise ExecutionError(self.configuration_error or "请先配置原模型后执行")
            if self.status in {"starting", "running", "cancelling"}:
                raise ExecutionBusy("当前任务仍在执行，请先停止或等待完成")
            os.close(open_workspace_directory(self.directory, self.directory_identity))
            previous = (
                self.status,
                self.error,
                self.final_response,
                self.turn_id,
                self.updated_at,
                self.title,
                self.turn_count,
            )
            self.status, self.error, self.final_response = "starting", None, ""
            self.turn_id = str(uuid4())
            turn = {
                "id": self.turn_id,
                "text": text.strip(),
                "max_calls": max_calls,
                "status": "starting",
            }
            self.turns.append(turn)
            self.turn_count += 1
            self.updated_at = time.time()
            if self.turn_count == 1:
                self.title = scrub(
                    text.strip().replace("\n", " "),
                    self.settings.get("MEDAGENT_LLM_API_KEY", ""),
                )[:32]
            try:
                self.persist(strict=True)
            except ExecutionError:
                self.turns.pop()
                (
                    self.status,
                    self.error,
                    self.final_response,
                    self.turn_id,
                    self.updated_at,
                    self.title,
                    self.turn_count,
                ) = previous
                raise
            self.event("task.submitted", turn_id=self.turn_id, text=turn["text"])
        threading.Thread(target=self._run, args=(turn,), daemon=True).start()
        return self.snapshot()

    def _send(self, payload):
        with self.send_lock:
            if not self.process or self.process.poll() is not None:
                raise ExecutionError("执行进程未运行")
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        if not shutil.which("bwrap") or not shutil.which("node"):
            raise ExecutionError("执行工作台需要 Linux bubblewrap 和 Node")
        self.ready.clear()
        environment = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "TMPDIR")
            if key in os.environ
        }
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            value = os.environ.get(key, "")
            proxy = urlsplit(value)
            if (
                proxy.scheme in {"http", "https"}
                and proxy.hostname
                and not proxy.username
                and not proxy.password
            ):
                environment[key] = value
        environment["NO_PROXY"] = "127.0.0.1,localhost,::1"
        self.process = subprocess.Popen(
            [
                shutil.which("node"),
                str(self.source_root / "integrations/dsh/execution-worker.mjs"),
            ],
            cwd=self.source_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=environment,
            start_new_session=True,
        )
        process = self.process
        self.reader_thread = threading.Thread(
            target=self._read, args=(process,), daemon=True
        )
        self.reader_thread.start()
        self._send(
            {
                "method": "configure",
                "id": self.id,
                "workspace": str(self.directory),
                "state": str(self.state_directory),
                "endpoint": self.settings["MEDAGENT_LLM_BASE_URL"],
                "api_key": self.settings["MEDAGENT_LLM_API_KEY"],
                "model": self.settings["MEDAGENT_LLM_MODEL"],
                "require_resume": self.require_resume,
                "python": sys.executable,
                "capability_binding": self.capability_binding,
                "user_turns": [
                    {"id": turn["id"], "text": turn["text"]}
                    for turn in self.turns[-32:]
                ],
            }
        )
        if not self.ready.wait(25):
            raise ExecutionError("隔离执行环境未能就绪")
        if process.poll() is not None or self.error:
            raise ExecutionError(self.error or "隔离执行环境启动失败")

    def _read(self, process):
        try:
            for line in process.stdout:
                if len(line) > 4_000_000:
                    raise ExecutionError("执行事件过大")
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    if process is not self.process:
                        break
                    kind = record.get("type", "runtime.unknown")
                    persist = False
                    if kind == "capability.completed":
                        try:
                            result = validate_receipt(record.get("result"))
                            if (
                                result["workspace_id"] != self.id
                                or result["turn_id"] != record.get("turn_id")
                                or not any(
                                    turn["id"] == result["turn_id"]
                                    for turn in self.turns
                                )
                            ):
                                raise ValueError()
                            if not self.capability_binding or any(
                                result[key] != self.capability_binding[key]
                                for key in ["registry_sha256", "implementation_sha256"]
                            ):
                                raise ValueError()
                        except (ValueError, TypeError, KeyError):
                            self.event(
                                "capability.failed",
                                error={
                                    "code": "invalid_host_receipt",
                                    "message": "未接收有效计算回执。",
                                },
                            )
                            continue
                        self.calculations.append(result)
                        self.calculations = self.calculations[-32:]
                        persist = True
                    self.event(
                        kind,
                        **{
                            k: v
                            for k, v in record.items()
                            if k not in {"sequence", "type"}
                        },
                    )
                    if kind == "ready":
                        self.ready.set()
                    elif kind == "model.request":
                        persist = True
                        for turn in self.turns:
                            if turn["id"] == record.get("turn_id"):
                                turn["model_calls"] = max(
                                    turn.get("model_calls", 0), record.get("index", 0)
                                )
                    elif kind == "jobs.snapshot":
                        self.jobs = record.get("jobs", [])
                    elif kind == "jobs.cancelled":
                        self.jobs = []
                        if self.status == "cancelling":
                            self.status = "cancelled"
                    elif kind == "worker.error":
                        self.error = record.get("diagnostic", "执行环境未成功")
                        self.ready.set()
                    elif record.get("turn_id") == self.turn_id:
                        if kind == "turn.started":
                            self.status = "running"
                        elif kind in {
                            "turn.completed",
                            "turn.failed",
                            "turn.cancelled",
                            "rejected",
                        }:
                            self.status = {
                                "turn.completed": "completed",
                                "turn.cancelled": "cancelled",
                            }.get(kind, "failed")
                            self.final_response = record.get("final_response", "")
                            self.error = record.get("error_type") or record.get(
                                "reason"
                            )
                            if self.turns:
                                self.turns[-1].update(
                                    status=self.status,
                                    final_response=self.final_response,
                                    model_calls=record.get("model_calls", 0),
                                )
                            persist = True
                    notification = record.get("notification", {})
                    if (
                        not self.require_resume
                        and notification.get("method") == "session.event"
                        and notification.get("params", {}).get("sessionId") == self.id
                        and notification.get("params", {}).get("event", {}).get("type")
                        == "turn/start"
                    ):
                        self.require_resume = True
                        persist = True
                    if persist:
                        self.persist()
        finally:
            process.stdout.close()
            with self.lock:
                if process is self.process and self.status in {
                    "starting",
                    "running",
                    "cancelling",
                }:
                    self.status, self.error = "failed", "执行进程意外结束"
                    self.event("worker.exited", exit_code=process.poll())
                    if self.turns:
                        self.turns[-1]["status"] = "interrupted"
                    self.persist()
                self.ready.set()

    def _run(self, turn):
        try:
            self._start()
            with self.lock:
                if self.closed or self.status == "cancelling":
                    self.status = "cancelled"
                    turn["status"] = "cancelled"
                    self.event("turn.cancelled", turn_id=turn["id"], model_calls=0)
                    return
                self._send(
                    {
                        "method": "run",
                        "id": turn["id"],
                        "text": turn["text"],
                        "max_calls": turn["max_calls"],
                    }
                )
        except Exception as exc:
            with self.lock:
                self.status = "failed"
                self.error = (
                    str(exc) if isinstance(exc, ExecutionError) else type(exc).__name__
                )
                turn["status"] = "failed"
                self.event("task.failed", error=self.error)
                self.persist()
            self._terminate()

    def cancel(self):
        with self.lock:
            if self.status not in {"starting", "running", "cancelling"} and not any(
                job["status"] in {"running", "stopping"} for job in self.jobs
            ):
                return self.snapshot()
            self.status = "cancelling"
            self.event("task.cancel_requested", turn_id=self.turn_id)
            if self.process and self.ready.is_set():
                self._send({"method": "cancel"})
        return self.snapshot()

    def _terminate(self):
        process = self.process
        if not process:
            return
        try:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=3)
        except (OSError, ProcessLookupError):
            pass
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if (
                self.reader_thread
                and self.reader_thread is not threading.current_thread()
            ):
                self.reader_thread.join(timeout=3)

    def close(self):
        with self.lock:
            self.closed = True
        try:
            self._terminate()
        finally:
            with self.lock:
                if self.directory_fd is not None:
                    os.close(self.directory_fd)
                    self.directory_fd = None
                self.persist()
                if self.lease is not None:
                    os.close(self.lease)
                    self.lease = None

    def artifacts(self):
        output = []
        visited = [0]

        def walk(directory, prefix="", depth=0):
            if depth > 8 or visited[0] >= 1000 or len(output) >= 200:
                return
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited[0] += 1
                    if visited[0] > 1000 or len(output) >= 200:
                        return
                    if entry.name.startswith(".") or entry.name in {
                        "node_modules",
                        "__pycache__",
                    }:
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        name = prefix + entry.name
                        if stat.S_ISREG(info.st_mode):
                            output.append({"path": name, "size": info.st_size})
                        elif stat.S_ISDIR(info.st_mode):
                            child = os.open(
                                entry.name,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory,
                            )
                            try:
                                walk(child, name + "/", depth + 1)
                            finally:
                                os.close(child)
                    except OSError:
                        continue

        try:
            directory = open_workspace_directory(
                self.directory, self.directory_identity
            )
        except ExecutionError as exc:
            self.storage_error = str(exc)
            return []
        self.storage_error = None
        try:
            walk(directory)
        finally:
            os.close(directory)
        return sorted(output, key=lambda item: item["path"])

    def read_file(self, relative):
        return safe_file(
            self.directory, relative, root_identity=self.directory_identity
        )

    def snapshot(self, after=0):
        with self.lock:
            if (
                self.process
                and self.process.poll() is None
                and self.ready.is_set()
                and not self.closed
                and time.monotonic() - self.last_inspection > 1
            ):
                self.last_inspection = time.monotonic()
                try:
                    self._send({"method": "inspect"})
                except (ExecutionError, OSError):
                    pass
            artifacts = self.artifacts()
            return {
                "id": self.id,
                "directory": str(self.directory),
                "runtime": "dsh-sdk",
                "dsh_version": "0.2.0-rc.2",
                "model": self.public_model,
                "status": self.status,
                "turn_id": self.turn_id,
                "final_response": self.final_response,
                "error": self.error,
                "sequence": self.sequence,
                "events": [event for event in self.events if event["sequence"] > after],
                "turns": list(self.turns),
                "jobs": list(self.jobs),
                "artifacts": artifacts,
                "storage_error": self.storage_error,
                "persistence_error": self.persistence_error,
                "execution_configured": self.execution_configured,
                "configuration_error": self.configuration_error,
                "restored": self.restored,
                "history_trimmed": self.history_trimmed,
                "title": self.title,
                "isolation": "bubblewrap-filesystem",
                "network": "enabled",
                "storage": "workspace_files_and_dsh_history_persist",
                "capability_binding": self.capability_binding,
                "calculations": list(self.calculations),
            }


class ExecutionManager:
    def __init__(self, root=None, source_root=None):
        self.source_root = Path(source_root or Path(__file__).resolve().parents[2])
        self.root = Path(
            root
            or os.environ.get(
                "MEDAGENT_EXECUTION_ROOT", str(self.source_root / "workspaces")
            )
        ).resolve()
        self.sessions = {}
        self.lock = threading.RLock()
        self.closed = False
        self.store = WorkspaceStore(self.root)

    def create(self, settings, registry=None):
        if not all(
            isinstance(settings.get(key), str) and settings[key]
            for key in (
                "MEDAGENT_LLM_BASE_URL",
                "MEDAGENT_LLM_API_KEY",
                "MEDAGENT_LLM_MODEL",
            )
        ):
            raise ExecutionError("请先在模型配置中填写服务地址、模型和密钥；默认离线演示不会调用模型")
        config = for_backend(settings, "dsh")
        route_identity(config)
        with self.lock:
            if self.closed:
                raise ExecutionError("执行工作台正在关闭")
            if len(self.sessions) >= 20:
                raise ExecutionError("本进程最多保留 20 个执行工作区")
            identifier = str(uuid4())
            lease = None
            try:
                lease = self.store.acquire(identifier)
                session = ExecutionSession(
                    identifier,
                    self.root,
                    self.source_root,
                    config,
                    store=self.store,
                    lease=lease,
                    capability_binding=freeze_binding(registry),
                )
                session.persist(strict=True)
            except Exception:
                if lease is not None:
                    os.close(lease)
                if "session" in locals():
                    session.lease = None
                    session.store = None
                    session.close()
                raise
            self.sessions[identifier] = session
            return session

    def open(self, identity, settings):
        with self.lock:
            if self.closed:
                raise ExecutionError("执行工作台正在关闭")
            if identity in self.sessions:
                with self.sessions[identity].lock:
                    self.sessions[identity].bind(settings)
                return self.sessions[identity]
            if len(self.sessions) >= 20:
                raise ExecutionError("本进程最多打开20个工作区，请重启后再打开其他工作区")
            config = dict(settings)
            lease = None
            try:
                record = self.store.read(identity)
                lease = self.store.acquire(identity)
                record = self.store.read(identity)
                session = ExecutionSession(
                    identity,
                    self.root,
                    self.source_root,
                    config,
                    reopened=True,
                    store=self.store,
                    lease=lease,
                    record=record,
                )
                session.persist(strict=True)
            except WorkspaceOccupied as exc:
                raise ExecutionBusy(str(exc)) from None
            except (WorkspaceStoreError, OSError) as exc:
                if lease is not None:
                    os.close(lease)
                raise ExecutionError("工作区记录或目录不可读取，未创建替代会话") from None
            except Exception:
                if lease is not None:
                    os.close(lease)
                if "session" in locals():
                    session.lease = None
                    session.store = None
                    session.close()
                raise
            self.sessions[identity] = session
            return session

    def catalog(self):
        with self.lock:
            try:
                saved = {r["id"]: r for r in self.store.catalog()}
            except (WorkspaceStoreError, OSError):
                raise ExecutionError("工作区记录目录不可读取") from None
            for session in self.sessions.values():
                with session.lock:
                    saved[session.id] = session.summary()
            return sorted(saved.values(), key=lambda r: r["updated_at"], reverse=True)

    def get(self, identifier):
        with self.lock:
            if identifier not in self.sessions:
                raise ExecutionError("执行工作区不存在")
            return self.sessions[identifier]

    def release(self, identifier):
        with self.lock:
            session = self.get(identifier)
            with session.lock:
                if session.status in {"starting", "running", "cancelling"} or any(
                    j["status"] in {"running", "stopping"} for j in session.jobs
                ):
                    raise ExecutionBusy("请先停止当前任务和后台工作，再关闭工作区")
            session.close()
            self.sessions.pop(identifier)

    def close(self):
        with self.lock:
            self.closed = True
            sessions = list(self.sessions.values())
        for session in sessions:
            session.close()
