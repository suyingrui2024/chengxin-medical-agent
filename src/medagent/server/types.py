"""Small server types (the threaded HTTP server)."""
from ._preamble import *  # noqa: F401,F403


class MedicalAgentServer(ThreadingHTTPServer):
    # A page loads fonts, CSS and an ES-module graph in parallel. Python 3.12's
    # default listen backlog of five can reset loopback connections during that
    # burst, leaving the module graph incomplete and the desktop uninitialized.
    request_queue_size = 64

    def __init__(
        self,
        address,
        handler,
        engine: ConsultationEngine,
        events: EventStore,
        backend_mode: str = "demo",
        access_token: Optional[str] = None,
        patient_store: Optional[PatientRecordStore] = None,
        image_recognizer=None,
    ):
        super().__init__(address, handler)
        self.engine = engine
        self.events = events
        self.backend_mode = backend_mode
        self.workspace = Workspace(engine, events, backend_mode)
        self.access_token = access_token
        self.execution = ExecutionManager()
        self._patient_store = patient_store
        self.image_recognizer = image_recognizer
        # P3d: one process-wide recognition job registry. Jobs are
        # in-memory by design; durable state lives in the store's
        # recognition_runs rows and entry statuses, so a restart loses
        # only the pollable job records (see the reset route).
        self.recognition_jobs = RecognitionJobStore()
        self.reviews = BackgroundReviewCoordinator(
            events, mode=os.environ.get("MEDAGENT_DSH_REVIEW_MODE", "shadow")
        )
        # Web/subagent advisory layer (docs/联网与子智能体工具设计.md):
        # assembled alongside the review coordinator and gated by env modes
        # that default to off, so stock deployments behave exactly as before.
        self.web_tools = WebTools(events)
        self.enricher = EducationEnricher(events, self.web_tools)
        self.subagents = SubagentCoordinator(
            events,
            getattr(engine, "backend", None),
            mode=os.environ.get("MEDAGENT_SUBAGENT_MODE", "off"),
            # Per-kind rollout overrides from the MEDAGENT_SUBAGENT_MODE_
            # <KIND> env keys (env_config's helper when available).
            kind_modes=subagent_kind_overrides(os.environ),
            web_enabled=os.environ.get("MEDAGENT_SUBAGENT_WEB", "off") == "on",
            web_tools=self.web_tools,
        )
        if isinstance(engine, AdaptiveConsultationEngine):
            engine.subagent_coordinator = self.subagents
        # The workspace recomposes engines on settings apply / capability
        # reconfigure; hand it the coordinator so every replacement
        # adaptive engine is wired too, not just the startup one.
        self.workspace.subagent_coordinator = self.subagents

    @property
    def patient_records(self) -> PatientRecordStore:
        if self._patient_store is None:
            self._patient_store = PatientRecordStore()
        return self._patient_store

    def server_close(self):
        reviews = getattr(self, "reviews", None)
        if reviews is not None:
            reviews.close()
        subagents = getattr(self, "subagents", None)
        if subagents is not None and hasattr(subagents, "close"):
            subagents.close()
        execution = getattr(self, "execution", None)
        if execution is not None:
            execution.close()
        super().server_close()
        workspace = getattr(self, "workspace", None)
        if workspace is not None:
            workspace.close()


__all__ = ["MedicalAgentServer"]
