"""Mixin: patient routes for Handler."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class HandlerPatient:
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
                len(parts) == 5
                and parts[0:2] == ["api", "patients"]
                and parts[3] == "trends"
            ):
                # P2 follow-up (P3/P4 batch): per-member trends, the
                # server-side root fix for the M2 residual. The default
                # route below keeps its legacy glucose shape untouched.
                kind = MEASUREMENT_SLUGS.get(parts[4])
                if kind is None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "档案接口不存在")
                    return
                patient = self.app.patient_records.patient(parts[2])
                if kind == "blood_glucose":
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
            if (
                len(parts) == 6
                and parts[0:2] == ["api", "patients"]
                and parts[3] == "entries"
                and parts[5] == "interpretation"
            ):
                # P3a canonical: latest ready sheet for one entry.
                self.app.patient_records.patient(parts[2])
                detail = self.app.patient_records.entry(parts[4])
                if detail["entry"]["patient_id"] != parts[2]:
                    raise PatientRecordError("该条目不属于该患者档案")
                sheet = self.app.patient_records.latest_interpretation(parts[4])
                if sheet is None or sheet.get("result") is None:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "interpretation_not_found",
                        "该条目还没有解读",
                    )
                    return
                self._json(
                    {
                        "analysis_id": sheet["analysis_id"],
                        "status": sheet["status"],
                        "model": sheet.get("model"),
                        "model_version": sheet.get("model_version"),
                        "interpretation": sheet["result"],
                    }
                )
                return
            if (
                len(parts) == 5
                and parts[0:2] == ["api", "patients"]
                and parts[2] == "entries"
                and parts[4] == "interpretation"
            ):
                # P3a frontend alias (patient-records-ui.js): same
                # sheet, addressed by entry alone.
                entry_id = parts[3]
                self.app.patient_records.entry(entry_id)
                sheet = self.app.patient_records.latest_interpretation(entry_id)
                if sheet is None or sheet.get("result") is None:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "interpretation_not_found",
                        "该条目还没有解读",
                    )
                    return
                self._json(
                    {
                        "analysis_id": sheet["analysis_id"],
                        "status": sheet["status"],
                        "model": sheet.get("model"),
                        "model_version": sheet.get("model_version"),
                        "interpretation": sheet["result"],
                    }
                )
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
                len(parts) == 5
                and parts[0:2] == ["api", "assets"]
                and parts[3] == "recognitions"
            ):
                # P3d: poll one async recognition job. The job must
                # belong to this asset, otherwise it is a 404, not a
                # leak of another asset's job record.
                try:
                    job = get_job(self.app.recognition_jobs, parts[4])
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "识别任务不存在")
                    return
                if job.get("asset_id") != parts[2]:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "识别任务不存在")
                    return
                self._json({"job": job})
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

    def _create_measurement(self, patient_id: str, kind: str, payload: dict) -> dict:
        """Validate one measurement body and persist it (P2b shapes).

        Shared by the default-member route and the per-member route
        (P2 follow-up): both enforce exactly the same body rules, so
        a member write can never slip past a check the default write
        would catch. Raises PatientRecordError on any shape problem.
        """
        if kind == "blood_glucose":
            allowed = {"value_text", "unit", "measured_at", "measurement_context"}
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
                "value_text": f"{payload['systolic']}/{payload['diastolic']}",
                "unit": payload["unit"],
                "measured_at": payload["measured_at"],
                "measurement_context": payload.get("measurement_context", "unknown"),
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
                "measurement_context": payload.get("measurement_context", "unknown"),
            }
        detail = self.app.patient_records.add_measurement(
            patient_id, kind=kind, **measurement_payload
        )
        self.app.events.append(
            Event(
                "patient-records",
                "patient.measurement_recorded",
                {"entry_id": detail["entry"]["entry_id"], "kind": kind},
            )
        )
        return detail

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
                (
                    len(parts) == 6
                    and parts[0:2] == ["api", "patients"]
                    and parts[3] == "entries"
                    and parts[5] == "interpretation"
                )
                or (
                    len(parts) == 5
                    and parts[0:2] == ["api", "patients"]
                    and parts[2] == "entries"
                    and parts[4] == "interpretations"
                )
            ):
                # P3a: grade one entry's confirmed observations only.
                # Canonical route carries the patient id; the frontend
                # alias (patient-records-ui.js) addresses the entry
                # alone and posts to the plural form. Both share this
                # handler so the sheet can never differ by address.
                # Explanations are the module's fixed templates: no new
                # model channel is opened for interpretation drafts.
                if payload:
                    raise PatientRecordError("解读请求不接受额外参数")
                if len(parts) == 6:
                    patient_id, entry_id = parts[2], parts[4]
                    self.app.patient_records.patient(patient_id)
                else:
                    entry_id = parts[3]
                    entry_owner = self.app.patient_records.entry(entry_id)
                    patient_id = entry_owner["entry"]["patient_id"]
                detail = self.app.patient_records.entry(entry_id)
                if detail["entry"]["patient_id"] != patient_id:
                    raise PatientRecordError("该条目不属于该患者档案")
                confirmed = [
                    item
                    for item in detail["observations"]
                    if item.get("kind") != "document_content"
                    and item.get("verification_status")
                    in ("patient_confirmed", "patient_corrected")
                    and item.get("value_text")
                ]
                if not confirmed:
                    raise PatientRecordError(
                        "该条目还没有经你确认的数值，确认后才可生成解读"
                    )
                sheet = build_interpretation(entry_id, confirmed)
                saved = self.app.patient_records.save_interpretation(
                    patient_id,
                    entry_id,
                    "template",
                    sheet.get("ranges_version") or "1.0",
                    [
                        item.get("current_revision_id")
                        for item in confirmed
                        if item.get("current_revision_id")
                    ],
                    sheet,
                )
                self._json(
                    {
                        "analysis_id": saved["analysis_id"],
                        "status": saved["status"],
                        "interpretation": sheet,
                    },
                    HTTPStatus.CREATED,
                )
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
                # N3: match the manual per-member measurement route —
                # archived members cannot take new measurements, even
                # via a confirmed transcription.
                owner = self.app.patient_records.patient(
                    detail["entry"]["patient_id"]
                )
                if owner.get("archived"):
                    raise PatientRecordError("已归档成员不能新增测量")
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
                patient = self.app.patient_records.ensure_default_patient()
                detail = self._create_measurement(
                    patient["patient_id"], kind, payload
                )
                self._json(detail, HTTPStatus.CREATED)
                return
            if (
                len(parts) == 5
                and parts[0:2] == ["api", "patients"]
                and parts[3] == "measurements"
            ):
                # P2 follow-up (P3/P4 batch): per-member measurements.
                # Same body rules as the default route, via the shared
                # helper; archived members cannot take new measurements.
                kind = MEASUREMENT_SLUGS.get(parts[4])
                if kind is None:
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "档案接口不存在")
                    return
                try:
                    patient = self.app.patient_records.patient(parts[2])
                except PatientRecordError:
                    self._error(
                        HTTPStatus.NOT_FOUND, "record_not_found", "患者档案不存在"
                    )
                    return
                if patient.get("archived"):
                    raise PatientRecordError("已归档成员不能新增测量")
                detail = self._create_measurement(
                    patient["patient_id"], kind, payload
                )
                self._json(detail, HTTPStatus.CREATED)
                return
            if (
                len(parts) == 5
                and parts[0:2] == ["api", "assets"]
                and parts[3] == "recognitions"
                and parts[4] == "reset"
            ):
                # P3d restart recovery: only when no job for this
                # entry is still active AND the entry is stuck in
                # recognizing. An active job means the entry may still
                # complete on its own, so reset is refused, not raced.
                if payload:
                    raise PatientRecordError("复位请求不接受额外参数")
                entry_id = self.app.patient_records.entry_id_for_asset(parts[2])
                active = [
                    job
                    for job in self.app.recognition_jobs.list_for_entry(entry_id)
                    if job.get("status") in ("queued", "running")
                ]
                if active:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "recognition_in_progress",
                        "该图片正在识别，暂不能复位",
                    )
                    return
                self._json(
                    self.app.patient_records.reset_recognition_orphan(parts[2])
                )
                return
            if (
                len(parts) == 4
                and parts[0:2] == ["api", "assets"]
                and parts[3] == "recognitions"
            ):
                # P3d async (the synchronous 201 path is retired):
                # validate first, then submit and return 202 at once.
                # Provider outcomes land on the job record, never as a
                # thrown error here; the entry status stays authoritative.
                # The old synchronous route is documented as superseded
                # by this one (see recognition_jobs.py); no sync path
                # remains in the server.
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
                entry_id = self.app.patient_records.entry_id_for_asset(parts[2])

                def _on_recognition_complete(job, asset_id=parts[2]):
                    if job.get("status") != "succeeded":
                        return
                    self.app.events.append(
                        Event(
                            "patient-records",
                            "patient.asset_recognized",
                            {
                                "entry_id": job.get("entry_id"),
                                "asset_id": asset_id,
                                "recognition_id": job.get("recognition_id"),
                            },
                        )
                    )

                try:
                    job = submit_job(
                        self.app.recognition_jobs,
                        self.app.patient_records,
                        recognizer,
                        entry_id=entry_id,
                        asset_id=parts[2],
                        image=image,
                        on_complete=_on_recognition_complete,
                    )
                except RecognitionJobError as exc:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "vision_unavailable",
                        str(exc),
                    )
                    return
                self._json(
                    {
                        "run_id": job["run_id"],
                        "entry_id": job["entry_id"],
                        "asset_id": job["asset_id"],
                        "status": job["status"],
                    },
                    HTTPStatus.ACCEPTED,
                )
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
