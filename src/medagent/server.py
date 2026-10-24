from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
from uuid import uuid4
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs

from .consultation import (
    ConsultationEngine,
    StaleActionError,
    UserInput,
    ConsultationBusyError,
)
from .domain import utc_now
from .events import Event, EventStore
from .agent import AdaptiveConsultationEngine
from .background_review import BackgroundReviewCoordinator
from .education import EducationEnricher
from .subagents import SubagentCoordinator
from .web_tools import WebTools
from .handoff import build_handoff
from .medical_images import (
    MedicalImageError,
    RecognitionError,
    decode_image_data_url,
    inspect_image,
    recognizer_from_settings,
)
from . import education as education_module
from .baseline_projection import build_baseline_projection
from .measurement_kinds import (
    MeasurementKindError,
    normalize_measurement,
    saveable_from_observation,
)
from .patient_records import (
    PatientRecordConflict,
    PatientRecordError,
    PatientRecordStore,
)
from .report_drafts import parse_report_draft
from .runtime import build_runtime
from .workspace import Workspace, StaleCompositionError, subagent_kind_overrides
from . import __version__
from .execution import ExecutionManager, ExecutionError, ExecutionBusy
from .execution_files import DirectoryChanged


MAX_BODY_BYTES = 64 * 1024
MAX_IMAGE_BODY_BYTES = 6 * 1024 * 1024


# P2b: frontend measurement slugs (patient-records-ui.js MEASUREMENT_KINDS)
# mapped to registry kinds. blood-glucose keeps its legacy route shape.
MEASUREMENT_SLUGS = {
    "blood-glucose": "blood_glucose",
    "blood-pressure": "blood_pressure",
    "temperature": "temperature",
    "weight": "weight",
    "blood-oxygen": "blood_oxygen",
}


def _default_baseline_projection(store) -> dict:
    """Build the P2c projection for the default (self) profile.

    Fail-open by contract: archive errors must never break session
    creation, so any failure yields an empty projection.
    """
    try:
        patient = store.ensure_default_patient()
        recent = store.recent_confirmed_measurements(patient["patient_id"])
        return build_baseline_projection(patient, recent)
    except Exception:
        return {}


def _session_baseline_projection(store, state=None) -> dict:
    """Build the P2c projection for a session's bound subject (P2d).

    A session bound via POST /api/sessions/{id}/bind-patient projects
    the bound member; an unbound session keeps the old semantics — the
    default (self) profile. Only an explicit snapshot binding counts:
    an unbound proxy session (consult_for != self, no binding) still
    gets the default projection at creation, exactly as before, and
    never an empty one via the attach-gate resolver. Fail-open like
    _default_baseline_projection.
    """
    try:
        bound = None
        snapshot = getattr(state, "runtime_snapshot", None)
        if isinstance(snapshot, dict):
            candidate = snapshot.get("consultation_patient_id")
            if isinstance(candidate, str) and candidate:
                bound = candidate
        if bound is not None:
            patient = store.patient(bound)
            recent = store.recent_confirmed_measurements(patient["patient_id"])
            return build_baseline_projection(patient, recent)
        return _default_baseline_projection(store)
    except Exception:
        return {}


def _consultation_patient_id(state, default_patient_id):
    """Resolve the archive owner for an attach gate (P2d).

    A session bound to a member (runtime_snapshot key written by a
    future member picker) resolves to that member; an unbound session
    keeps the old semantics — self consultations use the default
    profile, proxy consultations without a binding resolve to nothing
    and therefore cannot attach (same rejection as before, with a
    message that names the missing binding instead of demanding self).
    """
    snapshot = state.runtime_snapshot
    if isinstance(snapshot, dict):
        bound = snapshot.get("consultation_patient_id")
        if isinstance(bound, str) and bound:
            return bound
    if state.get("consult_for") == "self":
        return default_patient_id
    return None


def _persist_engine_state(engine, state) -> None:
    """Persist a state a route mutated directly, inside its session lock.

    These routes bypass the engine's own write-through points, so
    without this the change lives only in memory and a restart restores
    the stale snapshot. The engine's ``persist_state`` is fail-open by
    contract; the getattr guard only covers engines composed before the
    method existed.
    """
    persist_state = getattr(engine, "persist_state", None)
    if persist_state is not None:
        persist_state(state)


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


class Handler(BaseHTTPRequestHandler):
    # Bound how long a connection may take to deliver its request;
    # the threading server otherwise lets slow clients hold threads
    # indefinitely (audit S7).
    timeout = 30
    server_version = f"MedAgentPrototype/{__version__}"

    @property
    def app(self) -> MedicalAgentServer:
        return self.server  # type: ignore[return-value]

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        token = self.app.access_token
        if token is not None and not secrets.compare_digest(
            self.headers.get("X-MedAgent-Token", "").encode("utf-8"),
            token.encode("utf-8"),
        ):
            self._error(HTTPStatus.UNAUTHORIZED, "desktop_only", "此服务只接受当前桌面应用的请求")
            return False
        candidate = self.headers.get("X-Request-ID", "")
        self.request_id = (
            candidate
            if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", candidate)
            else str(uuid4())
        )
        return True

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    # Every verb funnels through an OSError guard: a disk failure inside
    # any route (event append, session store, patient records) used to
    # kill the handler thread with no response at all. The client now
    # gets a structured 500 it can surface and retry against.
    def do_GET(self) -> None:
        try:
            self._do_GET()
        except OSError:
            self._io_error()

    def do_POST(self) -> None:
        try:
            self._do_POST()
        except OSError:
            self._io_error()

    def do_DELETE(self) -> None:
        try:
            self._do_DELETE()
        except OSError:
            self._io_error()

    def do_PATCH(self) -> None:
        try:
            self._do_PATCH()
        except OSError:
            self._io_error()

    def _io_error(self) -> None:
        try:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "server_io_error",
                "服务器读写文件时出错，请稍后重试",
            )
        except OSError:
            # The connection itself is the casualty; nothing more can
            # be delivered on it.
            pass

    def _persistence_health(self) -> Dict[str, int]:
        """Durable-layer health for /api/health, read defensively: the
        counters live on the event store and the engines, and a missing
        counter reads as zero rather than breaking the health check."""
        events = self.app.events
        session_failures = 0
        for engine in getattr(self.app.workspace, "engines", []):
            value = getattr(engine, "_persist_failures", 0)
            if isinstance(value, dict):
                session_failures += sum(
                    int(item)
                    for item in value.values()
                    if isinstance(item, (int, float))
                )
            elif isinstance(value, (int, float)):
                session_failures += int(value)
        return {
            "event_append_failures": int(
                getattr(events, "append_failures", 0) or 0
            ),
            "event_skipped_corrupt": int(
                getattr(events, "skipped_corrupt", 0) or 0
            ),
            "session_persist_failures": session_failures,
        }

    def _maybe_schedule_education(self, engine, state) -> None:
        """Re-check the education layer when a snapshot is read.

        The enricher normally schedules at mutation time; a conclusion
        that became enrichable through a path that never scheduled (or
        a restart in between) would otherwise wait forever. The
        predicate is supplied by the education module and may not exist
        yet — its absence must never break a plain state read.
        """
        enricher = getattr(self.app, "enricher", None)
        if enricher is None:
            return
        predicate = getattr(enricher, "needs_enrichment", None)
        if predicate is None:
            predicate = getattr(education_module, "needs_enrichment", None)
        if predicate is None:
            return
        try:
            needed = predicate(state)
        except Exception:
            return
        if needed:
            enricher.schedule(engine, state)

    def _maybe_schedule_followup_drafts(self) -> None:
        """Re-check the follow-up draft batch when the due list is read.

        Drafts are generated inside this process by the workspace (the
        offline batch script writes to the same durable store), on a
        background thread, so the list response never waits on the
        model. A workspace composed before the trigger existed — or any
        failure inside it — must never break a plain list read.
        """
        workspace = getattr(self.app, "workspace", None)
        schedule = getattr(workspace, "maybe_schedule_followup_drafts", None)
        if schedule is None:
            return
        try:
            schedule()
        except Exception:
            return

    def _do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(
                {
                    "status": "ok",
                    "service": "medagent",
                    "version": __version__,
                    "backend": self.app.workspace.backend,
                    "execution_available": self.app.access_token is not None,
                    "persistence": self._persistence_health(),
                }
            )
            return
        if path == "/api/patients" or path.startswith("/api/patients/"):
            self._patient_get(path)
            return
        if path == "/api/patient" or path.startswith(
            ("/api/patient/", "/api/entries/", "/api/assets/")
        ):
            self._patient_get(path)
            return
        if path == "/api/execution" or path.startswith("/api/execution/"):
            self._execution_get(path)
            return
        if path == "/api/settings":
            settings = self.app.workspace.public_settings()
            settings["dsh_background_review_enabled"] = (
                self.app.reviews.enabled_for_process
            )
            self._json(settings)
            return
        if path == "/api/subagents/metrics":
            # Read-only telemetry aggregate over the event store; with
            # no coordinator activity it is the all-zero structure,
            # never an error.
            self._json(self.app.workspace.subagent_metrics())
            return
        if path == "/api/capabilities":
            self._json(self.app.workspace.catalog())
            return
        if path == "/api/sessions":
            self._json({"sessions": self.app.workspace.sessions()})
            return
        if path == "/api/follow-ups":
            self._maybe_schedule_followup_drafts()
            self._json({"follow_ups": self.app.workspace.follow_ups()})
            return
        if path == "/api/tasks":
            self._json(
                {
                    "tasks": list(self.app.workspace.engine.content.tasks.values()),
                    "versions": self.app.workspace.engine.content.versions,
                }
            )
            return
        if path.startswith("/api/sessions/"):
            parts = path.strip("/").split("/")
            try:
                engine = self.app.workspace.engine_for(parts[2])
                state = engine.state(parts[2])
            except (IndexError, KeyError):
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            if len(parts) == 4 and parts[3] == "events":
                self._json(
                    {
                        "events": [
                            event.to_dict()
                            for event in self.app.events.for_session(state.session_id)
                        ]
                    }
                )
            elif len(parts) == 4 and parts[3] == "snapshot":
                with engine._session_locks[state.session_id]:
                    self._maybe_schedule_education(engine, state)
                    self._json(
                        {
                            "state": state.to_dict(),
                            "events": [
                                event.to_dict()
                                for event in self.app.events.for_session(
                                    state.session_id
                                )
                            ],
                        }
                    )
            elif len(parts) == 4 and parts[3] == "handoff":
                with engine._session_locks[state.session_id]:
                    self._json({"handoff": build_handoff(state)})
            elif len(parts) == 3:
                with engine._session_locks[state.session_id]:
                    self._json({"state": state.to_dict()})
            else:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")
            return
        self._static(path)

    def _do_POST(self) -> None:
        if not self._same_origin():
            return
        path = urlparse(self.path).path
        if path == "/api/patients" or path.startswith("/api/patients/"):
            self._patient_post(path)
            return
        if path.startswith(
            ("/api/patient/", "/api/assets/", "/api/clarifications/", "/api/observations/")
        ):
            self._patient_post(path)
            return
        if path == "/api/execution" or path.startswith("/api/execution/"):
            self._execution_post(path)
            return
        if path == "/api/capabilities/configure" or path == "/api/capabilities/preview":
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                if path.endswith("/configure"):
                    result = self.app.workspace.reconfigure(payload)
                else:
                    if set(payload) != {
                        "id",
                        "input",
                        "expected_revision",
                    } or not isinstance(payload["id"], str):
                        raise ValueError("能力验证参数无效")
                    result = self.app.workspace.preview(
                        payload["id"], payload["input"], payload["expected_revision"]
                    )
            except StaleCompositionError as exc:
                self._error(HTTPStatus.CONFLICT, "stale_composition", str(exc))
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_capability", str(exc))
                return
            except TypeError:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_capability", "能力配置格式不正确")
                return
            except Exception:
                self._error(
                    HTTPStatus.BAD_GATEWAY, "capability_failed", "能力执行未成功，未写入病例"
                )
                return
            self._json(result)
            return
        if path in {"/api/settings", "/api/settings/test"}:
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "配置操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                result = (
                    self.app.workspace.probe(payload)
                    if path.endswith("/test")
                    else self.app.workspace.apply(payload)
                )
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_settings", str(exc))
                return
            except RuntimeError:
                self._error(
                    HTTPStatus.BAD_GATEWAY,
                    "connection_failed",
                    "连接未成功，请检查地址、协议、模型 ID 与密钥",
                )
                return
            except Exception:
                self._error(
                    HTTPStatus.BAD_GATEWAY,
                    "connection_failed",
                    "连接未成功，请检查地址、协议、模型 ID 与密钥",
                )
                return
            result["dsh_background_review_enabled"] = (
                self.app.reviews.enabled_for_process
            )
            self._json(result)
            return
        if path == "/api/reviews/enabled":
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            if set(payload) != {"enabled"} or not isinstance(payload["enabled"], bool):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_review_setting", "复核开关格式错误")
                return
            enabled = payload["enabled"]
            if not enabled:
                for session in self.app.workspace.sessions():
                    try:
                        engine = self.app.workspace.engine_for(session["session_id"])
                        with engine._lock_for(session["session_id"]):
                            self.app.reviews.invalidate(
                                engine.state(session["session_id"]), "disabled_by_user"
                            )
                    except KeyError:
                        continue
            self.app.reviews.set_enabled_for_process(enabled)
            self._json({"enabled": enabled})
            return
        if path == "/api/sessions":
            with self.app.workspace.lock:
                # Idempotent create: a retried request (same X-Request-ID)
                # returns the session it already created instead of
                # minting a duplicate consultation.
                replay = self.app.workspace.session_creation(self.request_id)
                if replay is None:
                    state, reply = self.app.workspace.engine.create_session(
                        request_id=self.request_id
                    )
                    # P2c: snapshot the session subject's baseline once
                    # at creation (fail-open: an archive failure leaves
                    # an empty projection, never a failed session). At
                    # creation no binding exists yet, so this is the
                    # default member's baseline; a later bind rebuilds
                    # it from the bound member (see bind-patient).
                    state.profile_baseline = _session_baseline_projection(
                        self.app.patient_records, state
                    )
                    state.baseline_reference_enabled = True
                    _persist_engine_state(self.app.workspace.engine, state)
                    replay = {
                        "session_id": state.session_id,
                        "reply": reply.to_dict(),
                        "state": state.to_dict(),
                    }
                    self.app.workspace.remember_session_creation(
                        self.request_id, replay
                    )
            self._json(replay, HTTPStatus.CREATED)
            return
        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "baseline-reference"
        ):
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            if set(payload) != {"enabled"} or not isinstance(payload["enabled"], bool):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_baseline_reference",
                    "档案引用开关格式错误",
                )
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    enabled = payload["enabled"]
                    state.baseline_reference_enabled = enabled
                    if enabled:
                        # Rebuild from the session's subject, not always
                        # the default: a bound member's session must not
                        # silently revert to the self profile's baseline
                        # on re-enable (same M2-class rebinding bug).
                        state.profile_baseline = _session_baseline_projection(
                            self.app.patient_records, state
                        )
                    else:
                        # Closing the reference clears the projection
                        # itself, so later state reads return nothing.
                        state.profile_baseline = {}
                        self.app.events.append(
                            Event(
                                state.session_id,
                                "baseline.reference_disabled",
                                {"session_id": state.session_id},
                            )
                        )
                    _persist_engine_state(engine, state)
                    response = {
                        "baseline_reference_enabled": enabled,
                        "state": state.to_dict(),
                    }
            except KeyError:
                self._error(
                    HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话"
                )
                return
            self._json(response)
            return

        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "bind-patient"
        ):
            # P2d minimal session binding (M4): the ONLY writer of the
            # consultation_patient_id snapshot key. It does not create
            # a second session concept and does not touch facts: the
            # binding is a snapshot subject pointer (consumed by the
            # attach gate and the baseline projection), never evidence.
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            if set(payload) != {"patient_id"} or not isinstance(
                payload["patient_id"], str
            ):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_bind_patient",
                    "绑定成员参数无效",
                )
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
            except KeyError:
                self._error(
                    HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话"
                )
                return
            try:
                patient = self.app.patient_records.patient(payload["patient_id"])
            except PatientRecordError:
                self._error(
                    HTTPStatus.NOT_FOUND, "record_not_found", "患者档案不存在"
                )
                return
            if patient.get("archived"):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_bind_patient",
                    "已归档的成员不能设为本次问诊对象",
                )
                return
            with engine._session_locks[parts[2]]:
                state = engine.state(parts[2])
                if not isinstance(state.runtime_snapshot, dict):
                    state.runtime_snapshot = {}
                state.runtime_snapshot["consultation_patient_id"] = patient[
                    "patient_id"
                ]
                # Rebuild the baseline from the bound member; when the
                # reference switch is off, keep the projection cleared
                # (the switch still gates every consumer).
                if state.baseline_reference_enabled:
                    state.profile_baseline = _session_baseline_projection(
                        self.app.patient_records, state
                    )
                else:
                    state.profile_baseline = {}
                _persist_engine_state(engine, state)
                response = {"patient": patient, "state": state.to_dict()}
            self._json(response)
            return

        parts = path.strip("/").split("/")
        if (
            len(parts) == 5
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "follow-up"
            and parts[4] == "outcome"
        ):
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            outcome = payload.get("outcome")
            note = payload.get("note", "")
            if not isinstance(outcome, str) or not isinstance(note, str):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_follow_up_outcome",
                    "随访结局参数无效",
                )
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                result = engine.record_follow_up_outcome(parts[2], outcome, note)
                state = engine.state(parts[2])
            except KeyError:
                self._error(
                    HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话"
                )
                return
            except ValueError as exc:
                self._error(
                    HTTPStatus.BAD_REQUEST, "invalid_follow_up_outcome", str(exc)
                )
                return
            self._json({**result, "follow_up": dict(state.follow_up)})
            return
        if (
            len(parts) == 6
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "patient-records"
            and parts[5] == "attach"
        ):
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            if payload:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_patient_record",
                    "健康档案带入参数无效",
                )
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    # P2d: the gate is "entry owner == consultation
                    # subject", resolved by _consultation_patient_id.
                    # Unbound sessions keep the old semantics exactly:
                    # self -> default profile, proxy -> rejected.
                    default_patient = self.app.patient_records.ensure_default_patient()
                    expected_patient_id = _consultation_patient_id(
                        state, default_patient["patient_id"]
                    )
                    if expected_patient_id is None:
                        raise PatientRecordError(
                            "本次问诊对象还没有绑定家庭成员档案，暂不能带入他人记录"
                        )
                    shared = self.app.patient_records.shareable_entry(parts[4])
                    if shared["entry"]["patient_id"] != expected_patient_id:
                        raise PatientRecordError("这条记录不属于本次问诊对象的健康档案")
                    observations = shared["observations"]
                    if not observations:
                        raise PatientRecordError(
                            "这条记录还没有经你确认的数据，请先完成核对"
                        )
                    observation_ids = {
                        item["observation_id"] for item in observations
                    }
                    state.report_items = [
                        item
                        for item in state.report_items
                        if item.get("source_observation_id") not in observation_ids
                    ]
                    entry = shared["entry"]
                    attached = []
                    for observation in observations:
                        confirmed = {
                            "report_item_id": (
                                f"patient-record:{observation['observation_id']}:"
                                f"{observation['current_revision_id']}"
                            ),
                            "name": observation["display_name"],
                            "reported_value": observation["value_text"],
                            "unit": observation["unit"] or "",
                            "reference_range": "",
                            "status": "confirmed_by_patient",
                            "source": "patient_record",
                            "source_entry_id": entry["entry_id"],
                            "source_observation_id": observation["observation_id"],
                            "source_revision_id": observation["current_revision_id"],
                            "record_title": entry["title"],
                            "measured_at": observation["measured_at"]
                            or entry["measured_at"],
                        }
                        state.report_items.append(confirmed)
                        attached.append(confirmed)
                    state.revision += 1
                    state.updated_at = utc_now()
                    latest_turn_id = next(
                        (
                            event.turn_id
                            for event in reversed(
                                self.app.events.for_session(state.session_id)
                            )
                            if event.event_type == "turn.agent_reply"
                        ),
                        None,
                    )
                    self.app.events.append(
                        Event(
                            state.session_id,
                            "patient_record.attached",
                            {
                                "entry_id": entry["entry_id"],
                                "observation_count": len(attached),
                                "revision_ids": [
                                    item["source_revision_id"] for item in attached
                                ],
                            },
                            turn_id=latest_turn_id,
                        )
                    )
                    self.app.reviews.invalidate(state, "patient_record_attached")
                    self.app.reviews.schedule(engine, state)
                    self.app.enricher.schedule(engine, state)
                    _persist_engine_state(engine, state)
                    response = {"items": attached, "state": state.to_dict()}
            except KeyError:
                self._error(
                    HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话"
                )
                return
            except PatientRecordError as exc:
                self._error(
                    HTTPStatus.BAD_REQUEST, "invalid_patient_record", str(exc)
                )
                return
            self._json(response)
            return
        if (
            len(parts) == 6
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "report-drafts"
            and parts[5] == "confirm"
        ):
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            source_item_id = payload.get("source_item_id")
            if set(payload) != {"source_item_id"} or not isinstance(source_item_id, str):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_report_item", "请指定要确认的报告项目")
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    draft = state.report_drafts.get(parts[4])
                    if not draft:
                        raise ValueError("报告草稿不存在或已失效")
                    draft_item = next(
                        (item for item in draft["items"] if item["source_item_id"] == source_item_id),
                        None,
                    )
                    if not draft_item:
                        raise ValueError("报告项目不存在")
                    item_id = f"{parts[4]}:{source_item_id}"
                    confirmed = {
                        "report_item_id": item_id,
                        "name": draft_item["name"],
                        "reported_value": draft_item["reported_value"],
                        "unit": draft_item["unit"],
                        "reference_range": draft_item["reference_range"],
                        "status": "confirmed_by_patient",
                    }
                    state.report_items = [
                        item for item in state.report_items if item.get("report_item_id") != item_id
                    ]
                    state.report_items.append(confirmed)
                    draft_item["status"] = "confirmed"
                    state.revision += 1
                    state.updated_at = utc_now()
                    self.app.events.append(
                        Event(
                            state.session_id,
                            "report.item_confirmed",
                            {"draft_id": parts[4], "source_item_id": source_item_id},
                        )
                    )
                    self.app.reviews.invalidate(state, "report_item_confirmed")
                    self.app.reviews.schedule(engine, state)
                    self.app.enricher.schedule(engine, state)
                    _persist_engine_state(engine, state)
                    response = {"item": confirmed, "state": state.to_dict()}
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_report_item", str(exc))
                return
            self._json(response)
            return
        if (
            len(parts) == 4
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "report-drafts"
        ):
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            if set(payload) - {"text", "format"} or not isinstance(payload.get("text"), str):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_report", "报告内容格式错误")
                return
            format_hint = payload.get("format", "auto")
            if not isinstance(format_hint, str):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_report", "报告格式错误")
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    draft = parse_report_draft(payload["text"], format_hint)
                    draft["draft_id"] = str(uuid4())
                    state.report_drafts[draft["draft_id"]] = draft
                    state.updated_at = utc_now()
                    self.app.events.append(
                        Event(
                            state.session_id,
                            "report.draft_created",
                            {
                                "draft_id": draft["draft_id"],
                                "source_format": draft["source_format"],
                                "item_count": len(draft["items"]),
                            },
                        )
                    )
                    _persist_engine_state(engine, state)
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_report", str(exc))
                return
            self._json({"draft": draft}, HTTPStatus.CREATED)
            return
        if (
            len(parts) == 4
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "messages"
        ):
            payload = self._read_json()
            if payload is None:
                return
            text = payload.get("text")
            choice_value = payload.get("choice_value")
            expected_action_id = payload.get("expected_action_id")
            calculation = payload.get("calculation")
            observation = payload.get("observation")
            if observation is not None and not isinstance(observation, dict):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_observation", "定位答案格式错误")
                return
            if calculation is not None and not isinstance(calculation, dict):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_calculation", "计算参数格式错误")
                return
            if not isinstance(text, str) or not text.strip():
                self._error(HTTPStatus.BAD_REQUEST, "invalid_message", "消息不能为空")
                return
            if choice_value is not None and not isinstance(choice_value, str):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_choice", "选项值格式错误")
                return
            if expected_action_id is not None and not isinstance(
                expected_action_id, str
            ):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_action_id", "问题标识格式错误")
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    self.app.reviews.invalidate(state, "new_submitted_message")
                    reply = engine.submit(
                        parts[2],
                        UserInput(
                            text=text,
                            choice_value=choice_value,
                            expected_action_id=expected_action_id,
                            calculation=calculation,
                            observation=observation,
                            request_id=self.request_id,
                        ),
                    )
                    self.app.reviews.schedule(engine, state)
                    self.app.enricher.schedule(engine, state)
                    response = {
                        "reply": reply.to_dict(),
                        "state": engine.state(parts[2]).to_dict(),
                    }
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ConsultationBusyError as exc:
                self._error(HTTPStatus.CONFLICT, "consultation_busy", str(exc))
                return
            except StaleActionError as exc:
                self._error(HTTPStatus.CONFLICT, "stale_action", str(exc))
                return
            self._json(response)
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    def _do_DELETE(self) -> None:
        if not self._same_origin():
            return
        path = urlparse(self.path).path
        if path.startswith("/api/entries/"):
            self._patient_delete(path)
            return
        parts = path.strip("/").split("/")
        if (
            len(parts) == 5
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "report-items"
        ):
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    before = len(state.report_items)
                    state.report_items = [
                        item
                        for item in state.report_items
                        if item.get("report_item_id") != parts[4]
                    ]
                    if len(state.report_items) == before:
                        raise ValueError("报告项目不存在")
                    state.revision += 1
                    state.updated_at = utc_now()
                    self.app.events.append(
                        Event(state.session_id, "report.item_removed", {"report_item_id": parts[4]})
                    )
                    self.app.reviews.invalidate(state, "report_item_removed")
                    _persist_engine_state(engine, state)
                    response = {"deleted": True, "state": state.to_dict()}
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_report_item", str(exc))
                return
            self._json(response)
            return
        if len(parts) == 3 and parts[0:2] == ["api", "sessions"]:
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    self.app.reviews.invalidate(state, "session_deleted")
                    self.app.workspace.delete_session(parts[2])
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            self._json({"deleted": True})
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    def _do_PATCH(self) -> None:
        if not self._same_origin():
            return
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0:2] == ["api", "patients"]:
            # Partial member baseline update (rename included). The
            # store validates the whole patch before writing, so an
            # invalid PATCH leaves stored data untouched.
            payload = self._read_json()
            if payload is None:
                return
            try:
                self.app.patient_records.patient(parts[2])
            except PatientRecordError:
                self._error(HTTPStatus.NOT_FOUND, "record_not_found", "患者档案不存在")
                return
            try:
                member = self.app.patient_records.update_patient(parts[2], payload)
            except PatientRecordError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_patient_record", str(exc))
                return
            self.app.events.append(
                Event(
                    "patient-records",
                    "patient.member_updated",
                    {
                        "patient_id": member["patient_id"],
                        "display_name": member["display_name"],
                    },
                )
            )
            self._json({"patient": member})
            return
        if (
            len(parts) == 6
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "facts"
            and parts[5] == "restore"
        ):
            payload = self._read_json()
            if payload is None:
                return
            expected_fact_id = payload.get("expected_fact_id")
            if not isinstance(expected_fact_id, str) or not expected_fact_id:
                self._error(
                    HTTPStatus.BAD_REQUEST, "invalid_fact_version", "请从最新记录撤销更正"
                )
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    self.app.reviews.invalidate(state, "fact_restored")
                    reply = engine.restore_fact(
                        parts[2], parts[4], expected_fact_id
                    )
                    self.app.reviews.schedule(engine, state)
                    self.app.enricher.schedule(engine, state)
                    response = {
                        "reply": reply.to_dict(),
                        "state": engine.state(parts[2]).to_dict(),
                    }
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ConsultationBusyError as exc:
                self._error(HTTPStatus.CONFLICT, "consultation_busy", str(exc))
                return
            except StaleActionError as exc:
                self._error(HTTPStatus.CONFLICT, "stale_fact", str(exc))
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_restore", str(exc))
                return
            self._json(response)
            return
        if (
            len(parts) == 5
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "facts"
        ):
            payload = self._read_json()
            if payload is None:
                return
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                self._error(HTTPStatus.BAD_REQUEST, "invalid_correction", "更正内容不能为空")
                return
            expected_fact_id = payload.get("expected_fact_id")
            if not isinstance(expected_fact_id, str) or not expected_fact_id:
                self._error(
                    HTTPStatus.BAD_REQUEST, "invalid_fact_version", "请从最新记录打开更正操作"
                )
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    self.app.reviews.invalidate(state, "fact_corrected")
                    reply = engine.correct_fact(
                        parts[2], parts[4], text.strip(), expected_fact_id
                    )
                    self.app.reviews.schedule(engine, state)
                    self.app.enricher.schedule(engine, state)
                    response = {
                        "reply": reply.to_dict(),
                        "state": engine.state(parts[2]).to_dict(),
                    }
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ConsultationBusyError as exc:
                self._error(HTTPStatus.CONFLICT, "consultation_busy", str(exc))
                return
            except StaleActionError as exc:
                self._error(HTTPStatus.CONFLICT, "stale_fact", str(exc))
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_correction", str(exc))
                return
            self._json(response)
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    def _read_json(self, max_bytes: int = MAX_BODY_BYTES) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_length", "请求长度无效")
            return None
        if length <= 0 or length > max_bytes:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_size", "请求内容过大或为空")
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "请求不是有效 JSON")
            return None
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "请求必须是 JSON 对象")
            return None
        return payload

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
            return False
        return True

    def _configured_recognizer(self):
        if self.app.image_recognizer is not None:
            return self.app.image_recognizer
        with self.app.workspace.lock:
            return recognizer_from_settings(
                dict(self.app.workspace.settings), self.app.workspace.backend
            )

    def _patient_get(self, path: str) -> None:
        try:
            parts = path.strip("/").split("/")
            if path == "/api/patients":
                self._json({"patients": self.app.patient_records.list_patients()})
                return
            if len(parts) == 3 and parts[0:2] == ["api", "patients"]:
                self._json({"patient": self.app.patient_records.patient(parts[2])})
                return
            if path == "/api/patient":
                default = self.app.patient_records.ensure_default_patient()
                self._json(
                    {
                        "patient": default,
                        "vision_available": self._configured_recognizer() is not None,
                    }
                )
                return
            if (
                len(parts) == 3
                and parts[0:2] == ["api", "patient"]
                and parts[2] == "timeline"
            ):
                patient = self.app.patient_records.ensure_default_patient()
                self._json(self.app.patient_records.timeline(patient["patient_id"]))
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "patients"]
                and parts[3] == "timeline"
            ):
                # P2d frontend: per-member timeline for the members view.
                self._json(self.app.patient_records.timeline(parts[2]))
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "patient"]
                and parts[2] == "trends"
            ):
                kind = MEASUREMENT_SLUGS.get(parts[3])
                if kind is None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "档案接口不存在")
                    return
                patient = self.app.patient_records.ensure_default_patient()
                if kind == "blood_glucose":
                    # Legacy route keeps its exact glucose-trend shape.
                    self._json(
                        self.app.patient_records.glucose_trend(patient["patient_id"])
                    )
                else:
                    self._json(
                        self.app.patient_records.measurement_trend(
                            kind, patient["patient_id"]
                        )
                    )
                return
            if len(parts) == 3 and parts[0:2] == ["api", "entries"]:
                self._json(self.app.patient_records.entry(parts[2]))
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "assets"]
                and parts[3] == "content"
            ):
                data, mime = self.app.patient_records.asset(parts[2])
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
                self.end_headers()
                self.wfile.write(data)
                return
            self._error(HTTPStatus.NOT_FOUND, "not_found", "档案接口不存在")
        except PatientRecordError as exc:
            self._error(HTTPStatus.NOT_FOUND, "record_not_found", str(exc))

    def _patient_post(self, path: str) -> None:
        if not self._same_origin():
            return
        parts = path.strip("/").split("/")
        payload = self._read_json(
            MAX_IMAGE_BODY_BYTES
            if len(parts) == 4
            and parts[0:2] == ["api", "patient"]
            and parts[2:] == ["entries", "images"]
            else MAX_BODY_BYTES
        )
        if payload is None:
            return
        try:
            if path == "/api/patients":
                # Create a member profile. Flat shape: display_name plus
                # any baseline field (birth_year, sex, height_cm,
                # weight_kg, chronic_conditions, allergies, medications,
                # surgical_history, birth_year_month). Unknown keys and
                # invalid values are rejected before any write.
                display_name = payload.pop("display_name", "新成员")
                member = self.app.patient_records.add_member(display_name, payload)
                self.app.events.append(
                    Event(
                        "patient-records",
                        "patient.member_created",
                        {
                            "patient_id": member["patient_id"],
                            "display_name": member["display_name"],
                        },
                    )
                )
                self._json({"patient": member}, HTTPStatus.CREATED)
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "patients"]
                and parts[3] == "archive"
            ):
                if payload:
                    raise PatientRecordError("归档请求不接受额外参数")
                try:
                    self.app.patient_records.patient(parts[2])
                except PatientRecordError:
                    self._error(
                        HTTPStatus.NOT_FOUND, "record_not_found", "患者档案不存在"
                    )
                    return
                member = self.app.patient_records.archive_patient(parts[2])
                self.app.events.append(
                    Event(
                        "patient-records",
                        "patient.member_archived",
                        {
                            "patient_id": member["patient_id"],
                            "display_name": member["display_name"],
                        },
                    )
                )
                self._json({"patient": member})
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "patient"]
                and parts[2:] == ["entries", "images"]
            ):
                if set(payload) - {
                    "title",
                    "measured_at",
                    "reported_at",
                    "filename",
                    "data_url",
                }:
                    raise PatientRecordError("图片归档参数无效")
                image = decode_image_data_url(payload.get("data_url"))
                patient = self.app.patient_records.ensure_default_patient()
                detail = self.app.patient_records.create_image_entry(
                    patient["patient_id"],
                    payload.get("title"),
                    payload.get("measured_at"),
                    payload.get("reported_at"),
                    payload.get("filename"),
                    image,
                )
                self.app.events.append(
                    Event(
                        "patient-records",
                        "patient.asset_archived",
                        {
                            "entry_id": detail["entry"]["entry_id"],
                            "asset_id": detail["asset_id"],
                            "mime_type": image.mime_type,
                            "byte_size": len(image.data),
                        },
                    )
                )
                self._json(detail, HTTPStatus.CREATED)
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "patient"]
                and parts[2:] == ["measurements", "from-observation"]
            ):
                # P2b: save a confirmed transcription as a measurement.
                # Never automatic: an inferred unit returns a
                # requires_confirmation response WITHOUT saving; only a
                # second call carrying an explicit unit persists. The
                # source observation is never touched.
                if (
                    not {"entry_id", "observation_id"}.issubset(payload)
                    or set(payload) - {"entry_id", "observation_id", "unit"}
                ):
                    raise PatientRecordError("另存为测量参数不完整")
                entry_id = payload["entry_id"]
                observation_id = payload["observation_id"]
                if not isinstance(entry_id, str) or not isinstance(observation_id, str):
                    raise PatientRecordError("另存为测量参数不完整")
                if "unit" in payload and not isinstance(payload["unit"], str):
                    raise PatientRecordError("另存为测量参数不完整")
                detail = self.app.patient_records.entry(entry_id)
                observation = next(
                    (
                        item
                        for item in detail["observations"]
                        if item["observation_id"] == observation_id
                    ),
                    None,
                )
                if observation is None:
                    raise PatientRecordError("观测记录不存在或不属于该条目")
                classification = saveable_from_observation(observation)
                if not classification["saveable"]:
                    raise PatientRecordError(
                        classification["reason"] or "该数值暂不能另存为测量"
                    )
                kind = classification["kind"]
                values = classification["values"]
                # N4: when the transcription text already carries an
                # explicit unit, a second submit carrying a different
                # unit must not silently reinterpret the number (e.g.
                # 37.2 °C resaved as °F). Aliases resolving to the same
                # canonical unit still pass; an invalid unit is left
                # for the normalize step below to reject as before.
                if not classification["unit_inferred"] and payload.get("unit"):
                    try:
                        claimed = normalize_measurement(
                            kind, values["reported_value"], payload["unit"]
                        )
                    except MeasurementKindError:
                        claimed = None
                    if (
                        claimed is not None
                        and claimed["reported_unit"] != classification["unit"]
                    ):
                        raise PatientRecordError(
                            "转录文本已标明单位，与提交的单位不一致，请核对后再提交"
                        )
                if classification["unit_inferred"] and not payload.get("unit"):
                    self._json(
                        {
                            "requires_confirmation": True,
                            "kind": kind,
                            "unit": classification["unit"],
                            "unit_inferred": True,
                            "extraction_rule": classification["extraction_rule"],
                            "value_text": values["reported_value"],
                            "entry_id": entry_id,
                            "observation_id": observation_id,
                        }
                    )
                    return
                unit = payload.get("unit") or classification["unit"]
                try:
                    normalized = normalize_measurement(
                        kind, values["reported_value"], unit
                    )
                except MeasurementKindError as exc:
                    raise PatientRecordError(str(exc)) from None
                measured_at = detail["entry"]["measured_at"] or utc_now()
                saved = self.app.patient_records.add_measurement(
                    detail["entry"]["patient_id"],
                    kind=kind,
                    value_text=normalized["reported_value"],
                    unit=unit,
                    measured_at=measured_at,
                    measurement_context="unknown",
                )
                self.app.events.append(
                    Event(
                        "patient-records",
                        "patient.measurement_saved_from_observation",
                        {
                            "entry_id": entry_id,
                            "observation_id": observation_id,
                            "kind": kind,
                        },
                    )
                )
                saved["from_observation"] = {
                    "entry_id": entry_id,
                    "observation_id": observation_id,
                    "unit_inferred": bool(classification["unit_inferred"]),
                    "unit": unit,
                }
                self._json(saved, HTTPStatus.CREATED)
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "patient"]
                and parts[2] == "measurements"
            ):
                kind = MEASUREMENT_SLUGS.get(parts[3])
                if kind is None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "档案接口不存在")
                    return
                if kind == "blood_glucose":
                    allowed = {
                        "value_text",
                        "unit",
                        "measured_at",
                        "measurement_context",
                    }
                    if set(payload) != allowed:
                        raise PatientRecordError("血糖记录参数不完整")
                    measurement_payload = dict(payload)
                elif kind == "blood_pressure":
                    if not {"systolic", "diastolic", "unit", "measured_at"}.issubset(
                        payload
                    ) or set(payload) - {
                        "systolic",
                        "diastolic",
                        "unit",
                        "measured_at",
                        "measurement_context",
                    }:
                        raise PatientRecordError("血压记录参数不完整")
                    measurement_payload = {
                        "value_text": (
                            f"{payload['systolic']}/{payload['diastolic']}"
                        ),
                        "unit": payload["unit"],
                        "measured_at": payload["measured_at"],
                        "measurement_context": payload.get(
                            "measurement_context", "unknown"
                        ),
                    }
                else:
                    if not {"value_text", "unit", "measured_at"}.issubset(
                        payload
                    ) or set(payload) - {
                        "value_text",
                        "unit",
                        "measured_at",
                        "measurement_context",
                    }:
                        raise PatientRecordError("测量记录参数不完整")
                    measurement_payload = {
                        "value_text": payload["value_text"],
                        "unit": payload["unit"],
                        "measured_at": payload["measured_at"],
                        "measurement_context": payload.get(
                            "measurement_context", "unknown"
                        ),
                    }
                patient = self.app.patient_records.ensure_default_patient()
                detail = self.app.patient_records.add_measurement(
                    patient["patient_id"], kind=kind, **measurement_payload
                )
                self.app.events.append(
                    Event(
                        "patient-records",
                        "patient.measurement_recorded",
                        {
                            "entry_id": detail["entry"]["entry_id"],
                            "kind": kind,
                        },
                    )
                )
                self._json(detail, HTTPStatus.CREATED)
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "assets"]
                and parts[3] == "recognitions"
            ):
                if payload:
                    raise PatientRecordError("识别请求不接受额外参数")
                recognizer = self._configured_recognizer()
                if recognizer is None:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "vision_unavailable",
                        "当前没有可用的视觉模型配置；图片已经安全归档，可稍后识别",
                    )
                    return
                raw, _ = self.app.patient_records.asset(parts[2])
                image = inspect_image(raw)
                recognition_id = self.app.patient_records.start_recognition(
                    parts[2], "deepseek", recognizer.model
                )
                try:
                    result = recognizer.recognize(image)
                    detail = self.app.patient_records.finish_recognition(
                        recognition_id, result
                    )
                except RecognitionError:
                    self.app.patient_records.fail_recognition(
                        recognition_id, "provider_failed"
                    )
                    raise
                except Exception:
                    self.app.patient_records.fail_recognition(
                        recognition_id, "provider_failed"
                    )
                    raise RecognitionError("视觉模型没有完成有效识别") from None
                self.app.events.append(
                    Event(
                        "patient-records",
                        "patient.asset_recognized",
                        {
                            "entry_id": detail["entry"]["entry_id"],
                            "asset_id": parts[2],
                            "recognition_id": recognition_id,
                            "observation_count": len(detail["observations"]),
                        },
                    )
                )
                self._json({"recognition_id": recognition_id, **detail}, HTTPStatus.CREATED)
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "clarifications"]
                and parts[3] == "answers"
            ):
                allowed = {"value_text", "unit", "expected_revision_id"}
                if set(payload) != allowed:
                    raise PatientRecordError("核对答案参数不完整")
                detail = self.app.patient_records.answer_clarification(parts[2], **payload)
                self.app.events.append(
                    Event(
                        "patient-records",
                        "patient.clarification_answered",
                        {"entry_id": detail["entry"]["entry_id"], "clarification_id": parts[2]},
                    )
                )
                self._json(detail)
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "observations"]
                and parts[3] == "revisions"
            ):
                required = {"value_text", "unit", "expected_revision_id"}
                if not required.issubset(payload) or set(payload) - (required | {"reason"}):
                    raise PatientRecordError("数据修改参数不完整")
                detail = self.app.patient_records.revise_observation(parts[2], **payload)
                self.app.events.append(
                    Event(
                        "patient-records",
                        "patient.observation_revised",
                        {"entry_id": detail["entry"]["entry_id"], "observation_id": parts[2]},
                    )
                )
                self._json(detail)
                return
            self._error(HTTPStatus.NOT_FOUND, "not_found", "档案接口不存在")
        except PatientRecordConflict as exc:
            self._error(HTTPStatus.CONFLICT, "stale_patient_record", str(exc))
        except (PatientRecordError, MedicalImageError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_patient_record", str(exc))
        except RecognitionError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, "recognition_failed", str(exc))

    def _patient_delete(self, path: str) -> None:
        if not self._same_origin():
            return
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0:2] != ["api", "entries"]:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "档案接口不存在")
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            if set(payload) != {"expected_version"}:
                raise PatientRecordError("删除请求缺少记录版本")
            self.app.patient_records.delete_entry(parts[2], payload["expected_version"])
            detached_sessions = []
            for session in self.app.workspace.sessions():
                session_id = session["session_id"]
                try:
                    engine = self.app.workspace.engine_for(session_id)
                    with engine._session_locks[session_id]:
                        state = engine.state(session_id)
                        retained = [
                            item
                            for item in state.report_items
                            if item.get("source_entry_id") != parts[2]
                        ]
                        if len(retained) == len(state.report_items):
                            continue
                        state.report_items = retained
                        state.revision += 1
                        state.updated_at = utc_now()
                        self.app.reviews.invalidate(state, "patient_record_deleted")
                        detached_sessions.append(session_id)
                        self.app.events.append(
                            Event(
                                session_id,
                                "patient_record.detached",
                                {"entry_id": parts[2], "reason": "record_deleted"},
                            )
                        )
                        _persist_engine_state(engine, state)
                except KeyError:
                    continue
            self.app.events.append(
                Event(
                    "patient-records",
                    "patient.entry_deleted",
                    {"entry_id": parts[2]},
                )
            )
            self._json(
                {"deleted": True, "detached_session_count": len(detached_sessions)}
            )
        except PatientRecordConflict as exc:
            self._error(HTTPStatus.CONFLICT, "stale_patient_record", str(exc))
        except PatientRecordError as exc:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_patient_record", str(exc))

    def _json(
        self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", getattr(self, "request_id", str(uuid4())))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json({"error": {"code": code, "message": message}}, status)

    def _execution_allowed(self):
        if self.app.access_token is None:
            self._error(HTTPStatus.FORBIDDEN, "desktop_required", "执行工作台仅在已鉴权的独立桌面中开放")
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "执行操作只允许来自当前应用")
            return False
        return True

    def _execution_get(self, path):
        if not self._execution_allowed():
            return
        try:
            parts = path.strip("/").split("/")
            if len(parts) == 2:
                self._json({"sessions": self.app.execution.catalog()})
                return
            session = self.app.execution.get(parts[2])
            query = parse_qs(urlparse(self.path).query)
            if len(parts) == 4 and parts[3] == "files":
                self._json(session.read_file(query.get("path", [""])[0]))
            elif len(parts) == 4 and parts[3] == "directory":
                hidden = query.get("hidden", ["0"])[0]
                if hidden not in {"0", "1"}:
                    raise ExecutionError("隐藏项参数无效")
                self._json(
                    session.list_directory(
                        query.get("path", [""])[0],
                        query.get("cursor", [None])[0],
                        int(query.get("limit", ["100"])[0]),
                        hidden == "1",
                    )
                )
            elif len(parts) == 4 and parts[3] == "preview":
                self._json(session.preview_file(query.get("path", [""])[0]))
            elif len(parts) == 4 and parts[3] == "artifact-status":
                self._json(
                    session.artifact_status(
                        query.get("path", [""])[0], query.get("sha256", [""])[0]
                    )
                )
            elif len(parts) == 4 and parts[3] == "image":
                data, mime = session.image_file(
                    query.get("path", [""])[0], query.get("sha256", [""])[0]
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy", "default-src 'none'; sandbox"
                )
                self.end_headers()
                self.wfile.write(data)
            elif len(parts) == 4 and parts[3] == "changes":
                self._json(
                    session.changes(
                        query.get("path", [None])[0],
                        limit=int(query.get("limit", ["200"])[0]),
                        offset=int(query.get("offset", ["0"])[0]),
                    )
                )
            elif len(parts) == 3:
                after = int(query.get("after", ["0"])[0])
                include = query.get("include_artifacts", ["1"])[0]
                if include not in {"0", "1"}:
                    raise ExecutionError("文件概览参数无效")
                self._json(
                    session.snapshot(max(0, after), include_artifacts=include == "1")
                )
            else:
                raise ExecutionError("执行接口不存在")
        except DirectoryChanged as exc:
            self._error(HTTPStatus.CONFLICT, "directory_changed", str(exc))
        except (ExecutionError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "execution_error", str(exc))

    def _execution_post(self, path):
        if not self._execution_allowed():
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            parts = path.strip("/").split("/")
            if len(parts) == 2 and not payload:
                with self.app.workspace.lock:
                    settings = dict(self.app.workspace.settings)
                    registry = self.app.workspace.engine.content.registry
                self._json(
                    self.app.execution.create(settings, registry=registry).snapshot(),
                    HTTPStatus.CREATED,
                )
                return
            if len(parts) != 4:
                raise ExecutionError("执行接口不存在")
            if parts[3] == "open" and not payload:
                with self.app.workspace.lock:
                    settings = dict(self.app.workspace.settings)
                self._json(self.app.execution.open(parts[2], settings).snapshot())
                return
            if parts[3] == "close" and not payload:
                self.app.execution.release(parts[2])
                self._json({"closed": True})
                return
            session = self.app.execution.get(parts[2])
            if parts[3] == "answer" and set(payload) == {
                "request_id",
                "turn_id",
                "answer",
            }:
                self._json(
                    session.answer_clarification(
                        payload["request_id"], payload["turn_id"], payload["answer"]
                    )
                )
                return
            if parts[3] == "runs" and not set(payload) - {
                "text",
                "max_calls",
                "time_budget_seconds",
            }:
                if not session.execution_configured:
                    with self.app.workspace.lock:
                        settings = dict(self.app.workspace.settings)
                    with session.lock:
                        session.bind(settings)
                self._json(
                    session.start_turn(
                        payload.get("text"),
                        payload.get("max_calls", 24),
                        payload.get("time_budget_seconds", 180),
                    ),
                    HTTPStatus.ACCEPTED,
                )
            elif parts[3] == "cancel" and not payload:
                self._json(session.cancel())
            else:
                raise ExecutionError("执行参数无效")
        except ExecutionBusy as exc:
            self._error(HTTPStatus.CONFLICT, "execution_busy", str(exc))
        except (ExecutionError, ValueError, OSError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "execution_error", str(exc))

    def _static(self, path: str) -> None:
        filename = "index.html" if path == "/" else path.removeprefix("/")
        if "/" in filename or filename.startswith("."):
            self._error(HTTPStatus.NOT_FOUND, "not_found", "文件不存在")
            return
        resource = resources.files("medagent.web").joinpath(filename)
        if not resource.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not_found", "文件不存在")
            return
        body = resource.read_bytes()
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            f"{mime}; charset=utf-8"
            if mime.startswith("text/") or mime == "application/javascript"
            else mime,
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    event_log: Optional[Path] = None,
    backend: Optional[str] = None,
) -> None:
    # The standalone server has no access token (only the desktop
    # sidecar injects one). Binding a non-loopback interface would
    # expose full consultation records to the network — refuse unless
    # the operator explicitly accepts that with MEDAGENT_ALLOW_REMOTE=1.
    if host not in {"127.0.0.1", "::1", "localhost"} and os.environ.get(
        "MEDAGENT_ALLOW_REMOTE"
    ) != "1":
        raise SystemExit(
            f"拒绝启动：--host {host} 不是回环地址，而独立服务没有访问令牌，"
            "问诊记录会暴露给网络。如确需对外绑定，请设置环境变量 "
            "MEDAGENT_ALLOW_REMOTE=1 并自行承担风险。"
        )
    runtime = build_runtime(event_log, backend)
    context = runtime.start()
    engine = context.services.require("consultation")
    events = context.services.require("events")
    httpd = MedicalAgentServer(
        (host, port), Handler, engine, events, context.services.require("backend_mode")
    )
    print(f"问诊智能体研究原型运行于 http://{host}:{port}")
    if getattr(engine, "session_store", None) is not None:
        print(
            "按 Ctrl+C 停止。已关闭的会话与随访计划会保存到本机并在重启后恢复；"
            "进行中的问诊不跨重启。这不是已经验证的医疗服务。"
        )
    else:
        print("按 Ctrl+C 停止。默认不持久化会话；这不是已经验证的医疗服务。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        runtime.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the outpatient consultation agent prototype"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--event-log",
        type=Path,
        default=None,
        help="Optional append-only JSONL event log",
    )
    parser.add_argument(
        "--backend", choices=["demo", "compatible", "dsh"], default=None
    )
    args = parser.parse_args()
    serve(args.host, args.port, args.event_log, args.backend)


if __name__ == "__main__":
    main()
