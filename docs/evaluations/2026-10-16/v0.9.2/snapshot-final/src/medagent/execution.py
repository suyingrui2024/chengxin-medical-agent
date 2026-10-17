"""Owned full-DSH workspaces. Clinical engines never receive these host tools."""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path, PurePosixPath
import select
import shutil
import signal
import stat
import subprocess
import threading
import time
from uuid import uuid4
from urllib.parse import urlsplit

from .env_config import for_backend


class ExecutionError(ValueError):
    pass


class ExecutionBusy(ExecutionError):
    pass


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
    def __init__(self, identifier, root, source_root, settings):
        self.id = identifier
        self.directory = root / identifier
        self.directory.mkdir(parents=True, mode=0o700)
        self.state_directory = root / ".state" / identifier
        self.state_directory.mkdir(parents=True, mode=0o700)
        # Retain the original inode until close, so deletion/recreation cannot
        # reuse its identity and silently adopt a different directory.
        self.directory_fd = open_workspace_directory(self.directory)
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
        self.ready = threading.Event()
        self.closed = False
        self.turns = []
        self.jobs = []
        self.last_inspection = 0

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
            if self.status in {"starting", "running", "cancelling"}:
                raise ExecutionBusy("当前任务仍在执行，请先停止或等待完成")
            os.close(open_workspace_directory(self.directory, self.directory_identity))
            self.status, self.error, self.final_response = "starting", None, ""
            self.turn_id = str(uuid4())
            turn = {
                "id": self.turn_id,
                "text": text.strip(),
                "max_calls": max_calls,
                "status": "starting",
            }
            self.turns.append(turn)
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
        threading.Thread(target=self._read, args=(process,), daemon=True).start()
        self._send(
            {
                "method": "configure",
                "id": self.id,
                "workspace": str(self.directory),
                "state": str(self.state_directory),
                "endpoint": self.settings["MEDAGENT_LLM_BASE_URL"],
                "api_key": self.settings["MEDAGENT_LLM_API_KEY"],
                "model": self.settings["MEDAGENT_LLM_MODEL"],
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
                "model": self.settings["MEDAGENT_LLM_MODEL"],
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
                "isolation": "bubblewrap-filesystem",
                "network": "enabled",
                "storage": "workspace_files_persist_process_session",
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

    def create(self, settings):
        if not all(
            settings.get(key)
            for key in (
                "MEDAGENT_LLM_BASE_URL",
                "MEDAGENT_LLM_API_KEY",
                "MEDAGENT_LLM_MODEL",
            )
        ):
            raise ExecutionError("请先在模型配置中填写服务地址、模型和密钥；默认离线演示不会调用模型")
        config = for_backend(settings, "dsh")
        with self.lock:
            if self.closed:
                raise ExecutionError("执行工作台正在关闭")
            if len(self.sessions) >= 20:
                raise ExecutionError("本进程最多保留 20 个执行工作区")
            identifier = str(uuid4())
            session = ExecutionSession(identifier, self.root, self.source_root, config)
            self.sessions[identifier] = session
            return session

    def get(self, identifier):
        with self.lock:
            if identifier not in self.sessions:
                raise ExecutionError("执行工作区不存在")
            return self.sessions[identifier]

    def close(self):
        with self.lock:
            self.closed = True
            sessions = list(self.sessions.values())
        for session in sessions:
            session.close()
