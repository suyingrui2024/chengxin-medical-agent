"""Owned full-DSH workspace session."""
from ._preamble import *  # noqa: F401,F403


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
        if self.process and self.process.poll() is None:
            return
        if (os.name != "nt" and not shutil.which("bwrap")) or not shutil.which("node"):
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
        from ..processes import popen
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
        startup_timeout_seconds = 45 if os.name == "nt" else 25
        if not self.ready.wait(startup_timeout_seconds):
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
                    if kind in {
                        "clarification.requested",
                        "clarification.answered",
                        "clarification.closed",
                    }:
                        if self._accept_clarification(kind, record):
                            self.persist()
                        continue
                    if kind == "clarification.answer_rejected":
                        for question in self.clarifications:
                            if (
                                question["id"] == record.get("request_id")
                                and question["turn_id"] == record.get("turn_id")
                                and question["status"] in ACTIVE_CLARIFICATION_STATES
                            ):
                                question.update(
                                    status="closed", reason="answer_rejected"
                                )
                                persist = True
                    if kind == "tool.observations":
                        try:
                            values = record["observations"]
                            if not isinstance(values, list) or len(values) > 64:
                                raise ValueError()
                            values = [validate_observation(value) for value in values]
                            if any(
                                not any(
                                    turn["id"] == value["turn_id"]
                                    for turn in self.turns
                                )
                                for value in values
                            ):
                                raise ValueError()
                        except (ValueError, TypeError, KeyError):
                            self.event(
                                "tool.observation_unavailable",
                                reason="invalid_runtime_observation",
                            )
                            continue
                        known = {value["observation_id"] for value in self.observations}
                        for value in values:
                            if value["observation_id"] in known:
                                continue
                            known.add(value["observation_id"])
                            self.observations.append(value)
                            self.event(
                                "tool.observed",
                                turn_id=value["turn_id"],
                                observation=value,
                            )
                        if len(self.observations) > 128:
                            self.history_trimmed = True
                            self.observations = self.observations[-128:]
                        self.persist()
                        continue
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
                    elif kind in {"model.request", "model.request_started"}:
                        persist = True
                        for turn in self.turns:
                            if turn["id"] == record.get("turn_id"):
                                turn["model_calls"] = max(
                                    turn.get("model_calls", 0), record.get("index", 0)
                                )
                    elif kind == "jobs.snapshot":
                        self.jobs = record.get("jobs", [])
                        self.agents = record.get("agents", [])
                    elif kind in {"jobs.cancelled", "turn.work_stopped"}:
                        if kind == "jobs.cancelled":
                            self.jobs = []
                            self.agents = []
                            self._close_clarifications(record.get("reason") or "user_cancelled")
                        for turn in self.turns:
                            if turn["id"] == record.get("turn_id", self.turn_id) and turn["status"] == "cancelling":
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
                    elif kind == "worker.error":
                        self.error = record.get("diagnostic", "执行环境未成功")
                        self.ready.set()
                    elif kind == "turn.deadline":
                        for turn in self.turns:
                            if turn["id"] == record.get("turn_id"):
                                turn["status"] = "cancelling"
                                persist = True
                        if record.get("turn_id") == self.turn_id:
                            self.status = "cancelling"
                            self.error = "time_budget_exhausted"
                            persist = True
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
                            if kind != "turn.completed":
                                self._close_clarifications(
                                    record.get("error_type") or "turn_stopped",
                                    record.get("turn_id"),
                                )
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
                if process is self.process and (
                    self.status
                    in {
                        "starting",
                        "running",
                        "cancelling",
                    }
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
        process = self.process
        if not process:
            return
        try:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        from ..processes import terminate
                        terminate(process)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        from ..processes import terminate
                        terminate(process)
                        process.wait(timeout=3)
        except (OSError, ProcessLookupError):
            pass
        finally:
            from ..processes import release
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
        output = []
        visited = [0]
        reasons = set()

        def walk(directory, prefix="", depth=0):
            if depth > 8:
                reasons.add("depth_limit")
                return
            if visited[0] >= 1000 or len(output) >= 200:
                reasons.add("entry_limit")
                return
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited[0] += 1
                    if visited[0] > 1000 or len(output) >= 200:
                        reasons.add("entry_limit")
                        return
                    if entry.name.startswith(".") or entry.name in {
                        "node_modules",
                        "__pycache__",
                    }:
                        continue
                    try:
                        entry.name.encode("utf-8")
                    except UnicodeEncodeError:
                        reasons.add("unsupported_name")
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        name = prefix + entry.name
                        if stat.S_ISREG(info.st_mode):
                            output.append(
                                {
                                    "path": name,
                                    "size": info.st_size,
                                    "revision": file_revision(info),
                                }
                            )
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
                        reasons.add("unreadable_entry")
                        continue

        try:
            directory = open_workspace_directory(
                self.directory, self.directory_identity
            )
        except ExecutionError as exc:
            self.storage_error = str(exc)
            return {
                "entries": [],
                "scan": {
                    "performed": True,
                    "complete": False,
                    "reasons": ["storage_unavailable"],
                },
            }
        self.storage_error = None
        try:
            try:
                walk(directory)
            except OSError:
                reasons.add("unreadable_directory")
        finally:
            os.close(directory)
        return {
            "entries": sorted(output, key=lambda item: item["path"]),
            "scan": {
                "performed": True,
                "complete": not reasons,
                "reasons": sorted(reasons),
                "visited_entries": visited[0],
                "scope": "visible regular files only; links and common caches excluded",
            },
        }

    def artifacts(self):
        return self.artifact_overview()["entries"]

    def list_directory(self, path="", cursor=None, limit=100, include_hidden=False):
        root = open_workspace_directory(self.directory, self.directory_identity)
        try:
            return directory_page(root, path, cursor, limit, include_hidden)
        except DirectoryChanged:
            raise
        except (OSError, ValueError) as error:
            raise ExecutionError(str(error)) from None
        finally:
            os.close(root)

    def read_file(self, relative):
        return safe_file(
            self.directory, relative, root_identity=self.directory_identity
        )

    def preview_file(self, relative):
        data, revision = safe_bytes(
            self.directory, relative, read_limit(relative), self.directory_identity
        )
        try:
            return artifact_projection(relative, data, revision)
        except ValueError as error:
            raise ExecutionError(str(error)) from None

    def image_file(self, relative, expected_sha256):
        data, revision = safe_bytes(
            self.directory, relative, IMAGE_LIMIT, self.directory_identity
        )
        try:
            preview = artifact_projection(relative, data, revision)
        except ValueError as error:
            raise ExecutionError(str(error)) from None
        if preview["kind"] != "image" or preview["sha256"] != expected_sha256:
            raise ExecutionError("图像已变化或不是支持的图片，请重新读取")
        return data, preview["mime"]

    def artifact_status(self, relative, expected_sha256):
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha256)
        ):
            raise ExecutionError("文件摘要无效")
        data, _ = safe_bytes(
            self.directory, relative, read_limit(relative), self.directory_identity
        )
        return {
            "schema_version": "artifact-freshness/v1",
            "path": relative,
            "matches": hashlib.sha256(data).hexdigest() == expected_sha256,
        }

    def _change_tracker(self):
        return ChangeTracker(
            self.directory,
            self.directory_identity,
            self.storage_root,
            self.id,
            self.settings.get("MEDAGENT_LLM_API_KEY", ""),
        )

    def changes(self, path=None, *, limit=200, offset=0):
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
                "isolation": "windows-processcontainer"
                if os.name == "nt"
                else "bubblewrap-filesystem",
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
