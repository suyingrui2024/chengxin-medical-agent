"""Mixin: agent routes for Handler."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class HandlerAgent:
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
        if path == "/api/medication-reminders/schedule":
            # P4: patient-set reminder times only. Times must all be
            # HH:MM; anything else is a 400, never silently dropped.
            # An empty list clears the schedule for that medication.
            # No dose is accepted, stored or returned anywhere here.
            payload = self._read_json()
            if payload is None:
                return
            name = payload.get("medication_name")
            times = payload.get("times")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(times, list)
                or any(
                    not isinstance(item, str) or not _TIME_RE.match(item.strip())
                    for item in times
                )
                or set(payload) - {"medication_name", "times"}
            ):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_medication_schedule",
                    "用药提醒时间须为 HH:MM 格式的时间列表",
                )
                return
            cleaned = []
            for item in times:
                item = item.strip()
                if item not in cleaned:
                    cleaned.append(item)
            store = self.app.patient_records
            schedules = _load_medication_schedules(store)
            schedules[name.strip().casefold()] = {
                "name": name.strip(),
                "times": cleaned,
            }
            _save_medication_schedules(store, schedules)
            self._json(
                {
                    "medication_name": name.strip(),
                    "scheduled_times": cleaned,
                    "status": "scheduled" if cleaned else "pending_setup",
                }
            )
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
                    # P3a attach extension: when the entry has a ready
                    # interpretation, its summary rides along as a
                    # comment only. It never overwrites reported_value
                    # or unit, and the grade never becomes an observation.
                    interpretation_note = None
                    try:
                        latest_sheet = (
                            self.app.patient_records.latest_interpretation(
                                entry["entry_id"]
                            )
                        )
                        if latest_sheet is not None and latest_sheet.get("result"):
                            interpretation_note = interpretation_summary(
                                latest_sheet["result"]
                            )
                    except Exception:
                        interpretation_note = None
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
                        if interpretation_note:
                            confirmed["interpretation_note"] = interpretation_note
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

    def _configured_recognizer(self):
        if self.app.image_recognizer is not None:
            return self.app.image_recognizer
        with self.app.workspace.lock:
            return recognizer_from_settings(
                dict(self.app.workspace.settings), self.app.workspace.backend
            )

    def _medication_reminders(self) -> list:
        """Reminder plan for the default profile (P4).

        Sources, deduplicated by normalized name inside
        build_medication_reminders: the default profile's baseline
        medications plus medications_taken from closed sessions. Times
        come only from medication_schedules.json (patient-set); a
        medication with no set time is pending_setup, never invented.
        No dose or interaction judgement is produced anywhere here.
        """
        store = self.app.patient_records
        default = store.ensure_default_patient()
        consultation_medications = []
        for summary in self.app.workspace.sessions():
            if summary.get("phase") != "closed":
                continue
            try:
                engine = self.app.workspace.engine_for(summary["session_id"])
                state = engine.state(summary["session_id"])
            except KeyError:
                continue
            value = state.get("medications_taken")
            if isinstance(value, str) and value:
                consultation_medications.append(value)
        schedules = _load_medication_schedules(store)
        scheduled_times: dict[str, list[str]] = {}
        candidates = [entry.get("name", "") for entry in default.get("medications", [])]
        candidates.extend(consultation_medications)
        for name in candidates:
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip().casefold()
            entry = schedules.get(key)
            if entry is not None:
                scheduled_times[name] = list(entry["times"])
        return build_medication_reminders(
            default.get("medications", []),
            consultation_medications,
            scheduled_times,
        )

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
