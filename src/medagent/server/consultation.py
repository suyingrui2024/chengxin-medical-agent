"""Mixin: consultation routes for Handler."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class HandlerConsultation:
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
        if path == "/api/medication-reminders":
            self._json({"reminders": self._medication_reminders()})
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
