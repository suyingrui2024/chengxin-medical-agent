"""Owned full-DSH workspace session."""
from collections import deque
import json
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4
from ..infra.env_config import for_backend
from ..infra.platform_fs import fcntl, os
from .artifact_access import WorkspaceArtifactAccess
from .changes import ChangeTracker
from .clarifications import (
    ACTIVE_CLARIFICATION_STATES,
    validate_clarification,
)
from .endpoint import route_identity, scrub
from .errors import ExecutionBusy, ExecutionError
from .identity import encode_identity
from .limits import DEFAULT_TIME_BUDGET_SECONDS, time_budget
from .observations import ACTIVE_JOB_STATES, validate_observation
from .worker_protocol import calculation_receipt, decode_record, observation_batch
from .projects import project_path as validate_project_path
from .store import (
    DSH_VERSION,
    FORMAT,
    WorkspaceOccupied,
    WorkspaceStoreError,
)
from .workspace_files import open_workspace_directory
from ..infra.platform import isolation_kind, startup_timeout_seconds, supports_sandbox


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
        project_path=None,
    ):
        """Build the session: resolve the workspace, open the state directory.

        Two identities are pinned at construction and verified on every later
        open -- the project directory's and the state directory's (dev, inode)
        pair -- so a directory that is renamed, replaced or swapped for a
        symlink between turns is refused rather than silently followed.

        A new session creates both directories private (0700); a reopened one
        takes its shape from the persisted record instead, and never creates
        anything.
        """
        self.id = identifier
        self.storage_root = root
        self.project_path = (record or {}).get("project_path", project_path)
        self.directory = (
            validate_project_path(self.project_path, root, source_root)
            if self.project_path is not None
            else root / identifier
        )
        if not reopened and self.project_path is None:
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
        if self.project_path is not None:
            try:
                fcntl.flock(self.directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(self.directory_fd)
                raise WorkspaceOccupied("此项目正在另一个执行工作区使用，请先关闭原工作区") from None
            except OSError:
                os.close(self.directory_fd)
                raise
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
        self.workspace_stopping = False
        self.turns = []
        self.jobs = []
        self.agents = []
        self.clarifications = [
            validate_clarification(value)
            for value in (record or {}).get("clarifications", [])
        ]
        for question in self.clarifications:
            if question["status"] in ACTIVE_CLARIFICATION_STATES:
                question.update(status="interrupted", reason="application_restart")
        self.observations = [
            validate_observation(value)
            for value in (record or {}).get("observations", [])
        ]
        self.capability_binding = (
            record.get("capability_binding") if record else capability_binding
        )
        self.calculations = list(record.get("calculations", [])) if record else []
        self.last_inspection = 0
        self.require_resume = reopened
        self.store, self.lease = store, lease
        self.created_at = self.updated_at = time.time()
        self.title = "未命名工作区"
        if self.project_path is not None:
            self.title = self.directory.name[:80]
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
                for observation in self.observations:
                    if observation["turn_id"] == turn["id"]:
                        self.event(
                            "tool.observed",
                            turn_id=turn["id"],
                            observation=observation,
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
        """Write the session record, with the model secret scrubbed from it.

        The history is trimmed from the front at thirty-two turns and each
        kept turn's free text is scrubbed of the API key and length-capped, so
        a long session cannot quietly grow the record past what the store
        accepts -- or worse, persist the credential that the scrub exists to
        keep out.

        A session without a store or a lease is in-memory only and skips the
        write; that is the demo shape, not an error.
        """
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
                    "time_budget_seconds",
                    "changes_recorded",
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
            "observations": json.loads(
                scrub(json.dumps(self.observations[-128:]), secret)
            ),
            "clarifications": json.loads(
                scrub(json.dumps(self.clarifications[-32:], ensure_ascii=False), secret)
            ),
        }
        if self.project_path is not None:
            record["project_path"] = str(self.directory)
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
        record["observations"] = [
            value for value in record["observations"] if value["turn_id"] in retained
        ]
        record["clarifications"] = [
            value for value in record["clarifications"] if value["turn_id"] in retained
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

    def start_turn(
        self, text, max_calls=24, time_budget_seconds=DEFAULT_TIME_BUDGET_SECONDS
    ):
        """Validate and open one turn: the only entry point for new work.

        The text and the call ceiling are checked first, then the state -- a
        closed workspace, an unconfigured model, or a turn already running all
        refuse before anything is created, because half-starting a turn would
        leave the worker owning work nobody asked for.

        The workspace identity is re-verified here too, so a directory swapped
        between turns is caught at turn start rather than at first file read.
        """
        try:
            time_budget_seconds = time_budget(time_budget_seconds)
        except ValueError as exc:
            raise ExecutionError(str(exc)) from None
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
            if (
                self.status in {"starting", "running", "cancelling"}
                or self.has_pending_clarification()
            ):
                raise ExecutionBusy("当前任务仍在执行，请先停止或等待完成")
            os.close(open_workspace_directory(self.directory, self.directory_identity))
            next_turn = str(uuid4())
            tracker = self._change_tracker()
            try:
                baseline = tracker.begin(next_turn)
            except (ValueError, OSError):
                raise ExecutionError("本轮变更基线未能保存，执行尚未开始；请检查工作目录与磁盘空间。") from None
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
            self.turn_id = next_turn
            turn = {
                "id": self.turn_id,
                "text": text.strip(),
                "max_calls": max_calls,
                "time_budget_seconds": time_budget_seconds,
                "changes_recorded": True,
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
                try:
                    tracker.discard(next_turn)
                except OSError:
                    pass
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
            try:
                tracker.prune([t["id"] for t in self.turns[-32:]])
            except OSError:
                pass
            self.event("workspace.baseline", turn_id=self.turn_id, **baseline)
            self.event("task.submitted", turn_id=self.turn_id, text=turn["text"])
            self.workspace_stopping = False
        threading.Thread(target=self._run, args=(turn,), daemon=True).start()
        return self.snapshot()

    def _send(self, payload):
        with self.send_lock:
            if not self.process or self.process.poll() is not None:
                raise ExecutionError("执行进程未运行")
            try:
                self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                # Shutdown can close stdin after poll() but before this write.
                # Keep inspection available so it can report the original failure.
                raise ExecutionError("执行进程通信已关闭") from None

    def has_pending_clarification(self):
        return any(
            value["status"] in ACTIVE_CLARIFICATION_STATES
            for value in self.clarifications
        )

    def _clarification_turn_live(self, turn_id):
        if self.closed or self.workspace_stopping:
            return False
        turn = next((value for value in self.turns if value["id"] == turn_id), None)
        if turn is None:
            return False
        status = self.status if turn_id == self.turn_id else turn.get("status")
        return status not in {"cancelling", "failed", "cancelled", "interrupted"}

    def answer_clarification(self, request_id, turn_id, answer):
        """Answer a pending clarification, idempotently.

        The answer is length-capped, control-character-free and scrubbed of the
        model secret before anything else happens.  Re-submitting the same
        answer to an already-answered question returns the snapshot unchanged
        -- a retry after a flaky network is not a second answer.

        The question must belong to this workspace and to a still-live turn; a
        clarification whose turn has ended can no longer be answered.
        """
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or len(answer) > 2000
            or any(ord(c) < 32 and c not in "\n\t" for c in answer)
        ):
            raise ExecutionError("请填写不超过2000字的回答")
        answer = scrub(answer.strip(), self.settings.get("MEDAGENT_LLM_API_KEY", ""))
        with self.lock:
            question = next(
                (
                    value
                    for value in self.clarifications
                    if value["id"] == request_id and value["turn_id"] == turn_id
                ),
                None,
            )
            if question is None:
                raise ExecutionError("此问题不属于当前工作区或回合")
            if (
                question["status"] in {"submitted", "answered"}
                and question["answer"] == answer
            ):
                return self.snapshot()
            if (
                question["status"] != "pending"
                or not self._clarification_turn_live(turn_id)
                or time.time() >= question["deadline_at"]
                or not self.process
                or self.process.poll() is not None
            ):
                raise ExecutionError("此问题已失效，请在后续任务中重新说明需求")
            os.close(open_workspace_directory(self.directory, self.directory_identity))
            updated = {**question, "status": "submitted", "answer": answer}
            try:
                validate_clarification(updated)
            except ValueError:
                raise ExecutionError("澄清回答格式无效") from None
            original = dict(question)
            question.update(updated)
            try:
                self.persist(strict=True)
                self._send(
                    {
                        "method": "clarification.answer",
                        "request_id": request_id,
                        "turn_id": turn_id,
                        "answer": answer,
                    }
                )
            except (ExecutionError, OSError):
                question.update(original)
                self.persist()
                raise ExecutionError("回答未能确认送达，请重试；未自动继续执行。") from None
            self.event(
                "clarification.submitted", turn_id=turn_id, request_id=request_id
            )
            return self.snapshot()

    def _accept_clarification(self, kind, record):
        """Take a clarification event from the worker, or refuse it.

        The record is validated, tied to a turn this session owns, and checked
        against what is already held: a request for an unknown or already-open
        question, past its deadline, or addressed to a finished turn is
        dropped -- the worker will re-ask if it still matters.

        Answers and closures only ever update a question this session has seen,
        so a forged event cannot create state out of nothing.
        """
        try:
            value = validate_clarification(record.get("clarification"))
            if value["turn_id"] != record.get("turn_id") or not any(
                turn["id"] == value["turn_id"] for turn in self.turns
            ):
                return False
        except (ValueError, TypeError, KeyError):
            return False
        previous = next(
            (item for item in self.clarifications if item["id"] == value["id"]), None
        )
        if kind == "clarification.requested":
            if (
                previous is not None
                or value["status"] != "pending"
                or time.time() >= value["deadline_at"]
                or not self._clarification_turn_live(value["turn_id"])
            ):
                return False
            self.clarifications.append(value)
            self.clarifications = self.clarifications[-32:]
        else:
            if (
                previous is None
                or previous["status"] not in ACTIVE_CLARIFICATION_STATES
            ):
                return False
            if any(
                value[key] != previous[key]
                for key in value
                if key not in {"status", "answer", "reason"}
            ):
                return False
            if kind == "clarification.answered" and (
                value["status"] != "answered"
                or previous["status"] != "submitted"
                or value["answer"] != previous["answer"]
            ):
                return False
            if kind == "clarification.closed" and value["status"] != "closed":
                return False
            if kind == "clarification.closed" and value["answer"] is None:
                value["answer"] = previous["answer"]
            previous.update(value)
        self.event(kind, turn_id=value["turn_id"], clarification=value)
        return True

    def _close_clarifications(self, reason, turn_id=None):
        for value in self.clarifications:
            if value["status"] in ACTIVE_CLARIFICATION_STATES and (
                turn_id is None or value["turn_id"] == turn_id
            ):
                value.update(status="closed", reason=reason)

    def _start(self):
        """Spawn the execution worker, with the environment it is allowed to have.

        The worker needs Linux bubblewrap and Node -- checked here, where the
        failure can still become a turn error rather than a half-open process.
        The environment is an allowlist: PATH and locale, plus proxy variables
        only when they carry no credentials, and loopback addresses are pinned
        into NO_PROXY so the worker's traffic to the local server cannot be
        redirected through a proxy.
        """
        if self.process and self.process.poll() is None:
            return
        if not supports_sandbox():
            raise ExecutionError("执行工作台需要 Linux bubblewrap 和 Node")
        self.ready.clear()
        environment = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "TMPDIR", "SystemRoot", "WINDIR", "TEMP", "TMP")
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
        from ..infra.processes import popen
        self.process = popen(
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
                "directory_identity": encode_identity(self.directory_identity),
                "store_root": str(self.storage_root),
                "user_turns": [
                    {"id": turn["id"], "text": turn["text"]}
                    for turn in self.turns[-32:]
                ]
                + [
                    {"id": question["id"], "text": question["answer"]}
                    for question in self.clarifications
                    if question["status"] == "answered"
                ],
            }
        )
        # The first packaged ProcessContainer startup on Windows can be slower
        # while Defender inspects the bundled native runtime.  Keep a bounded,
        # platform-specific owner timeout that leaves the SDK enough time to
        # report its own initialization result.
        timeout = startup_timeout_seconds()
        if not self.ready.wait(timeout):
            raise ExecutionError("隔离执行环境未能就绪")
        if process.poll() is not None or self.error:
            raise ExecutionError(self.error or "隔离执行环境启动失败")

    def _read(self, process):
        """Read one worker's stream, applying each valid record under the lock.

        A replaced worker cannot write into its successor's state. Parsing
        has no state effects; acceptance and persistence stay with this
        session, and stream closure always releases startup waiters.
        """
        try:
            for line in process.stdout:
                record = decode_record(line)
                if record is None:
                    continue
                with self.lock:
                    if process is not self.process:
                        break
                    self._accept_worker_record(record)
        finally:
            process.stdout.close()
            with self.lock:
                self._worker_exited(process)

    def _accept_worker_record(self, record):
        """Apply one record while holding the session lock; validators cannot mutate state."""
        kind = record.get("type", "runtime.unknown")
        if kind in {
            "clarification.requested",
            "clarification.answered",
            "clarification.closed",
        }:
            if self._accept_clarification(kind, record):
                self.persist()
            return
        if kind == "tool.observations":
            self._accept_observation_batch(record)
            return
        if kind == "capability.completed" and not self._accept_calculation(record):
            return
        persist = kind == "capability.completed"
        if kind == "clarification.answer_rejected":
            for question in self.clarifications:
                if (
                    question["id"] == record.get("request_id")
                    and question["turn_id"] == record.get("turn_id")
                    and question["status"] in ACTIVE_CLARIFICATION_STATES
                ):
                    question.update(status="closed", reason="answer_rejected")
                    persist = True
        self.event(
            kind, **{k: v for k, v in record.items() if k not in {"sequence", "type"}}
        )
        persist = self._update_runtime_status(kind, record) or persist
        if (
            not self.require_resume
            and record.get("notification", {}).get("method") == "session.event"
            and record["notification"].get("params", {}).get("sessionId") == self.id
            and record["notification"]["params"].get("event", {}).get("type")
            == "turn/start"
        ):
            self.require_resume = True
            persist = True
        if persist:
            self.persist()

    def _accept_observation_batch(self, record):
        """Validate the entire batch before accepting any observation, then deduplicate it."""
        try:
            values = observation_batch(record, turn_ids={turn["id"] for turn in self.turns})
        except (ValueError, TypeError, KeyError):
            self.event("tool.observation_unavailable", reason="invalid_runtime_observation")
            return
        known = {value["observation_id"] for value in self.observations}
        for value in values:
            if value["observation_id"] in known:
                continue
            known.add(value["observation_id"])
            self.observations.append(value)
            self.event("tool.observed", turn_id=value["turn_id"], observation=value)
        if len(self.observations) > 128:
            self.history_trimmed = True
            self.observations = self.observations[-128:]
        self.persist()

    def _accept_calculation(self, record):
        try:
            result = calculation_receipt(
                record,
                workspace_id=self.id,
                turn_ids={turn["id"] for turn in self.turns},
                capability_binding=self.capability_binding,
            )
        except (ValueError, TypeError, KeyError):
            self.event(
                "capability.failed",
                error={
                    "code": "invalid_host_receipt",
                    "message": "未接收有效计算回执。",
                },
            )
            return False
        self.calculations.append(result)
        self.calculations = self.calculations[-32:]
        return True

    def _update_runtime_status(self, kind, record):
        """Dispatch lifecycle events and report whether the owner must persist."""
        if kind == "ready":
            self.ready.set()
        elif kind in {"model.request", "model.request_started"}:
            for turn in self.turns:
                if turn["id"] == record.get("turn_id"):
                    turn["model_calls"] = max(
                        turn.get("model_calls", 0), record.get("index", 0)
                    )
            return True
        elif kind == "jobs.snapshot":
            self.jobs = record.get("jobs", [])
            self.agents = record.get("agents", [])
        elif kind in {"jobs.cancelled", "turn.work_stopped"}:
            return self._accept_work_stopped(kind, record)
        elif kind == "worker.error":
            self.error = record.get("diagnostic", "执行环境未成功")
            self.ready.set()
        elif kind == "turn.deadline":
            return self._accept_deadline(record)
        elif record.get("turn_id") == self.turn_id:
            return self._accept_turn_status(kind, record)
        return False

    def _accept_work_stopped(self, kind, record):
        """Stop facts close pending questions without treating an earlier turn as current."""
        persist = False
        if kind == "jobs.cancelled":
            self.jobs = []
            self.agents = []
            self._close_clarifications(record.get("reason") or "user_cancelled")
        for turn in self.turns:
            if (
                turn["id"] == record.get("turn_id", self.turn_id)
                and turn["status"] == "cancelling"
            ):
                turn["status"] = "cancelled"
                persist = True
        if (
            self.status == "cancelling"
            and record.get("turn_id", self.turn_id) == self.turn_id
        ):
            self.status = "cancelled"
            if self.turns:
                self.turns[-1]["status"] = "cancelled"
            if record.get("reason") == "time_budget_exhausted":
                self.error = "time_budget_exhausted"
            persist = True
        return persist

    def _accept_deadline(self, record):
        persist = False
        for turn in self.turns:
            if turn["id"] == record.get("turn_id"):
                turn["status"] = "cancelling"
                persist = True
        if record.get("turn_id") == self.turn_id:
            self.status = "cancelling"
            self.error = "time_budget_exhausted"
            persist = True
        return persist

    def _accept_turn_status(self, kind, record):
        """Update only the current turn; starting does not establish durable SDK history."""
        if kind == "turn.started":
            self.status = "running"
        elif kind in {"turn.completed", "turn.failed", "turn.cancelled", "rejected"}:
            self.status = {
                "turn.completed": "completed",
                "turn.cancelled": "cancelled",
            }.get(kind, "failed")
            if kind != "turn.completed":
                self._close_clarifications(
                    record.get("error_type") or "turn_stopped", record.get("turn_id")
                )
            self.final_response = record.get("final_response", "")
            self.error = record.get("error_type") or record.get("reason")
            if self.turns:
                self.turns[-1].update(
                    status=self.status,
                    final_response=self.final_response,
                    model_calls=record.get("model_calls", 0),
                )
            return True
        return False

    def _worker_exited(self, process):
        """Only the currently owned worker may interrupt live work on stream closure."""
        if process is self.process and (
            self.status in {"starting", "running", "cancelling"}
            or self.has_pending_clarification()
            or (bool(self.agents) and not self.closed)
        ):
            self._close_clarifications("worker_exited")
            self.status, self.error = "failed", "执行进程意外结束"
            self.event("worker.exited", exit_code=process.poll())
            if self.turns:
                self.turns[-1]["status"] = "interrupted"
            self.persist()
        if process is self.process:
            self.agents = []
        self.ready.set()

    def _run(self, turn):
        """Send one turn to the worker, or record why the turn never started.

        The worker is started first; a session that was closed or cancelled
        while that happened becomes "cancelled" with a cancelled turn, not an
        orphaned run.  Anything raised on the way -- a failed start, a closed
        pipe -- becomes a failed turn with a message the patient can act on,
        and never propagates out of the worker thread.
        """
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
                        "time_budget_seconds": turn.get(
                            "time_budget_seconds", DEFAULT_TIME_BUDGET_SECONDS
                        ),
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
            if (
                self.status not in {"starting", "running", "cancelling"}
                and not self.has_pending_clarification()
                and not any(job["status"] in ACTIVE_JOB_STATES for job in self.jobs)
                and not self.agents
            ):
                return self.snapshot()
            self.workspace_stopping = True
            self.status = "cancelling"
            self.event("task.cancel_requested", turn_id=self.turn_id)
            if self.process and self.ready.is_set():
                self._send({"method": "cancel"})
        return self.snapshot()

    def _terminate(self):
        """Stop the worker, gracefully first, and release what it held.

        Closing stdin asks the worker to exit; six seconds later it is
        terminated, and three after that killed -- a worker stuck in a build
        gets every chance to flush before the last resort.  The process and
        the reader thread are released even when the termination path raised,
        because a leaked worker holds the workspace lease.
        """
        process = self.process
        if not process:
            return
        try:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    from ..infra.processes import terminate_gracefully

                    terminate_gracefully(process)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        from ..infra.processes import terminate
                        terminate(process)
                        process.wait(timeout=3)
        except (OSError, ProcessLookupError):
            pass
        finally:
            from ..infra.processes import release
            release(process)
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

    def artifact_overview(self):
        try:
            result = self._artifact_access().overview()
        except ExecutionError as error:
            self.storage_error = str(error)
            return {
                "entries": [],
                "scan": {
                    "performed": True,
                    "complete": False,
                    "reasons": ["storage_unavailable"],
                },
            }
        self.storage_error = None
        return result

    def artifacts(self):
        return self.artifact_overview()["entries"]

    def _artifact_access(self):
        return WorkspaceArtifactAccess(self.directory, self.directory_identity)

    def list_directory(self, path="", cursor=None, limit=100, include_hidden=False):
        return self._artifact_access().list_directory(path, cursor, limit, include_hidden)

    def read_file(self, relative):
        return self._artifact_access().read_file(relative)

    def preview_file(self, relative):
        return self._artifact_access().preview_file(relative)

    def image_file(self, relative, expected_sha256):
        return self._artifact_access().image_file(relative, expected_sha256)

    def artifact_status(self, relative, expected_sha256):
        return self._artifact_access().artifact_status(relative, expected_sha256)

    def _change_tracker(self):
        return ChangeTracker(
            self.directory,
            self.directory_identity,
            self.storage_root,
            self.id,
            self.settings.get("MEDAGENT_LLM_API_KEY", ""),
        )

    def changes(self, path=None, *, limit=200, offset=0):
        """Report what the last turn changed in the workspace.

        Three honest states: "not_started" when no turn exists,
        "baseline_unavailable" when the turn's baseline is gone, and the full
        observation otherwise.  A transient read failure becomes an
        ExecutionError -- the caller can retry -- rather than an empty answer
        that would read as "nothing changed".
        """
        with self.lock:
            turn_id = self.turns[-1]["id"] if self.turns else None
            tracker = self._change_tracker()
        if not turn_id:
            return {
                "schema": "workspace-changes/v1",
                "state": "not_started",
                "workspace_id": self.id,
                "turn_id": None,
                "entries": [],
                "counts": None,
            }
        try:
            return {
                "state": "observed",
                **tracker.observe(turn_id, path, limit=limit, offset=offset),
            }
        except FileNotFoundError:
            return {
                "schema": "workspace-changes/v1",
                "state": "baseline_unavailable",
                "workspace_id": self.id,
                "turn_id": turn_id,
                "entries": [],
                "counts": None,
            }
        except (OSError, ValueError, KeyError, TypeError):
            raise ExecutionError("本轮文件变更暂不可用，请检查基线和工作目录。") from None

    def snapshot(self, after=0, include_artifacts=True):
        """Build the reply the poll endpoint returns, and nudge the worker.

        While a turn is running, an inspect request is sent at most once a
        second so the artifact list stays current without a request per poll.
        Without artifacts the workspace identity is re-verified instead -- the
        cheap check that catches a replaced directory even when nothing else
        was asked.

        Everything is read under the session lock, so a snapshot never mixes
        the state of one turn with the transcript of another.
        """
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
            if include_artifacts:
                overview = self.artifact_overview()
            else:
                try:
                    fd = open_workspace_directory(
                        self.directory, self.directory_identity
                    )
                    os.close(fd)
                    self.storage_error = None
                except ExecutionError as error:
                    self.storage_error = str(error)
                overview = {
                    "entries": [],
                    "scan": {"performed": False, "complete": None, "reasons": []},
                }
            return {
                "id": self.id,
                "directory": str(self.directory),
                "workspace_kind": "project"
                if self.project_path is not None
                else "managed",
                "runtime": "dsh-sdk",
                "dsh_version": "0.2.1-rc.2",
                "model": self.public_model,
                "status": self.status,
                "turn_id": self.turn_id,
                "final_response": self.final_response,
                "error": self.error,
                "sequence": self.sequence,
                "events": [event for event in self.events if event["sequence"] > after],
                "turns": list(self.turns),
                "jobs": list(self.jobs),
                "agents": [dict(agent) for agent in self.agents],
                "artifacts": overview["entries"],
                "artifact_scan": overview["scan"],
                "storage_error": self.storage_error,
                "persistence_error": self.persistence_error,
                "execution_configured": self.execution_configured,
                "configuration_error": self.configuration_error,
                "restored": self.restored,
                "history_trimmed": self.history_trimmed,
                "title": self.title,
                "isolation": isolation_kind(),
                "network": "enabled",
                "storage": "workspace_files_and_dsh_history_persist",
                "capability_binding": self.capability_binding,
                "calculations": list(self.calculations),
                "clarifications": [dict(value) for value in self.clarifications],
                "change_baseline_available": bool(
                    self.turns and self.turns[-1].get("changes_recorded")
                ),
            }


__all__ = ["ExecutionSession"]
