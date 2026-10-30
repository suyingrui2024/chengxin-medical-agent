"""Manager that owns the collection of execution sessions."""
from ._preamble import *  # noqa: F401,F403
from .session import ExecutionSession


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

    def create(self, settings, registry=None, *, project_path=None):
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
                    project_path=project_path,
                )
                session.persist(strict=True)
            except Exception as exc:
                if lease is not None:
                    os.close(lease)
                if "session" in locals():
                    session.lease = None
                    session.store = None
                    session.close()
                if isinstance(exc, WorkspaceOccupied):
                    raise ExecutionBusy(str(exc)) from None
                raise
            self.sessions[identifier] = session
            return session

    def attach_project(self, directory, settings, registry=None):
        """Trusted host entry point. HTTP cannot supply a filesystem path."""
        directory = validate_project_path(directory, self.root, self.source_root)
        with self.lock:
            if self.closed:
                raise ExecutionError("执行工作台正在关闭")
            for session in self.sessions.values():
                if session.project_path is not None and session.directory == directory:
                    with session.lock:
                        os.close(
                            open_workspace_directory(
                                directory, session.directory_identity
                            )
                        )
                        session.bind(settings)
                    return session
            for saved in self.store.catalog():
                if saved.get("project_path") == str(directory):
                    return self.open(saved["id"], settings)
            return self.create(settings, registry, project_path=str(directory))

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
                if lease is not None:
                    os.close(lease)
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
                if (
                    session.status in {"starting", "running", "cancelling"}
                    or session.has_pending_clarification()
                    or any(j["status"] in ACTIVE_JOB_STATES for j in session.jobs)
                    or bool(session.agents)
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


__all__ = ["ExecutionManager"]
