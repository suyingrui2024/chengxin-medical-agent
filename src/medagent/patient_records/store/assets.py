"""Mixin: assets for PatientRecordStore."""
from ._preamble import *  # noqa: F401,F403


class StoreAssets:
    def _require_entry(self, connection: sqlite3.Connection, entry_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM medical_entries WHERE entry_id = ? AND deleted_at IS NULL",
            (entry_id,),
        ).fetchone()
        if row is None:
            raise PatientRecordError("医疗记录不存在")
        return row

    def create_image_entry(
        self,
        patient_id: str,
        title: Any,
        measured_at: Any,
        reported_at: Any,
        original_filename: Any,
        image: InspectedImage,
    ) -> dict[str, Any]:
        entry_id = str(uuid4())
        asset_id = str(uuid4())
        now = utc_now()
        name = _clean_text(title or "医疗图片", "记录标题", 120)
        filename = _clean_text(original_filename or f"image{image.extension}", "文件名", 180)
        measured = _time(measured_at, "测量时间")
        reported = _time(reported_at, "报告时间")
        relative = Path("assets") / f"{asset_id}{image.extension}"
        destination = self.root / relative
        temporary = self.assets / f".{asset_id}.uploading"
        with self.lock:
            try:
                with temporary.open("xb") as handle:
                    handle.write(image.data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
                try:
                    destination.chmod(0o600)
                except OSError:
                    pass
                with self._transaction() as connection:
                    self._require_patient(connection, patient_id)
                    connection.execute(
                        """INSERT INTO medical_entries
                           (entry_id, patient_id, entry_type, title, measured_at, reported_at,
                            uploaded_at, status, version, deleted_at)
                           VALUES (?, ?, 'uploaded_document', ?, ?, ?, ?, 'awaiting_recognition', 1, NULL)""",
                        (entry_id, patient_id, name, measured, reported, now),
                    )
                    connection.execute(
                        """INSERT INTO source_assets
                           (asset_id, entry_id, relative_path, original_filename, sha256,
                            mime_type, byte_size, width, height, page_count, created_at, deleted_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL)""",
                        (
                            asset_id,
                            entry_id,
                            relative.as_posix(),
                            filename,
                            sha256(image.data).hexdigest(),
                            image.mime_type,
                            len(image.data),
                            image.width,
                            image.height,
                            now,
                        ),
                    )
                    self._bump_patient(connection, patient_id, now)
            except Exception:
                temporary.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                raise
        detail = self.entry(entry_id)
        detail["asset_id"] = asset_id
        return detail

    def _asset_row(self, connection: sqlite3.Connection, asset_id: str) -> sqlite3.Row:
        row = connection.execute(
            """SELECT a.*, e.patient_id, e.status AS entry_status
               FROM source_assets a JOIN medical_entries e ON e.entry_id = a.entry_id
               WHERE a.asset_id = ? AND a.deleted_at IS NULL AND e.deleted_at IS NULL""",
            (asset_id,),
        ).fetchone()
        if row is None:
            raise PatientRecordError("图片材料不存在")
        return row

    def asset(self, asset_id: str) -> tuple[bytes, str]:
        with self._read() as connection:
            row = self._asset_row(connection, asset_id)
        path = (self.root / row["relative_path"]).resolve()
        if path.parent != self.assets or path.is_symlink() or not path.is_file():
            raise PatientRecordError("图片材料不可用")
        data = path.read_bytes()
        if sha256(data).hexdigest() != row["sha256"]:
            raise PatientRecordError("图片材料校验失败")
        return data, row["mime_type"]

    def start_recognition(self, asset_id: str, provider: str, model: str) -> str:
        recognition_id = str(uuid4())
        now = utc_now()
        with self._transaction() as connection:
            row = self._asset_row(connection, asset_id)
            if row["entry_status"] == "recognizing":
                raise PatientRecordConflict("这张图片正在识别")
            connection.execute(
                """INSERT INTO recognition_runs
                   (recognition_id, asset_id, provider, model, contract_version, status,
                    result_json, created_at, completed_at, error_code)
                   VALUES (?, ?, ?, ?, 'medical-image-recognition/v1', 'running', NULL, ?, NULL, NULL)""",
                (recognition_id, asset_id, provider, model, now),
            )
            connection.execute(
                "UPDATE medical_entries SET status = 'recognizing' WHERE entry_id = ?",
                (row["entry_id"],),
            )
        return recognition_id

    @staticmethod
    def _display_name(context: str, value_text: str | None) -> str:
        prefix = context
        if value_text and value_text in prefix:
            prefix = prefix.split(value_text, 1)[0]
        else:
            prefix = re.split(r"[<>≤≥]?\s*\d|\?", prefix, maxsplit=1)[0]
        prefix = prefix.strip(" |:：,，;；-—")
        return prefix[:80] or "识别数值"

    @staticmethod
    def _insert_observation(
        connection: sqlite3.Connection,
        *,
        entry: sqlite3.Row,
        kind: str,
        display_name: str,
        value_text: str | None,
        unit: str,
        context: str,
        clarity: str,
        verification_status: str,
        source_type: str,
        source_id: str,
        now: str,
    ) -> str:
        observation_id = str(uuid4())
        revision_id = str(uuid4())
        connection.execute(
            """INSERT INTO observations
               (observation_id, entry_id, kind, display_name, value_text, numeric_value,
                unit, context, measurement_context, clarity, verification_status,
                source_type, source_id, measured_at, current_revision_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id,
                entry["entry_id"],
                kind,
                display_name,
                value_text,
                unit,
                context,
                clarity,
                verification_status,
                source_type,
                source_id,
                entry["measured_at"],
                revision_id,
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO observation_revisions
               (revision_id, observation_id, previous_revision_id, value_text, unit,
                context, author_type, reason, created_at)
               VALUES (?, ?, NULL, ?, ?, ?, ?, 'initial_extraction', ?)""",
            (revision_id, observation_id, value_text, unit, context, source_type, now),
        )
        return observation_id

    def finish_recognition(
        self, recognition_id: str, result: Any
    ) -> dict[str, Any]:
        normalized = validate_recognition(result)
        now = utc_now()
        with self._transaction() as connection:
            run = connection.execute(
                """SELECT r.*, a.entry_id, e.patient_id, e.measured_at
                   FROM recognition_runs r
                   JOIN source_assets a ON a.asset_id = r.asset_id
                   JOIN medical_entries e ON e.entry_id = a.entry_id
                   WHERE r.recognition_id = ? AND e.deleted_at IS NULL""",
                (recognition_id,),
            ).fetchone()
            if run is None or run["status"] != "running":
                raise PatientRecordConflict("图片识别任务已经结束或失效")
            existing = connection.execute(
                "SELECT 1 FROM recognition_runs WHERE asset_id = ? AND status = 'succeeded'",
                (run["asset_id"],),
            ).fetchone()
            if existing:
                raise PatientRecordConflict("这张图片已经有有效识别结果")
            entry = connection.execute(
                "SELECT * FROM medical_entries WHERE entry_id = ?", (run["entry_id"],)
            ).fetchone()
            needs_confirmation = False
            for number in normalized["numbers"]:
                unclear = number["clarity"] != "clear"
                observation_id = self._insert_observation(
                    connection,
                    entry=entry,
                    kind="document_number",
                    display_name=self._display_name(number["context"], number["value_text"]),
                    value_text=number["value_text"],
                    unit="",
                    context=number["context"],
                    clarity=number["clarity"],
                    verification_status="needs_confirmation" if unclear else "model_extracted",
                    source_type="model_extracted_document",
                    source_id=f"{recognition_id}:{number['number_id']}",
                    now=now,
                )
                if unclear:
                    needs_confirmation = True
                    connection.execute(
                        """INSERT INTO clarification_requests
                           (clarification_id, observation_id, reason, question,
                            alternatives_json, status, answered_revision_id, created_at, answered_at)
                           VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)""",
                        (
                            str(uuid4()),
                            observation_id,
                            number["uncertainty_reason"],
                            f"请核对：{number['context']}",
                            json.dumps(number["alternatives"], ensure_ascii=False),
                            now,
                        ),
                    )
            content = normalized["content"]
            content_unclear = content["clarity"] != "clear"
            content_observation = self._insert_observation(
                connection,
                entry=entry,
                kind="document_content",
                display_name="整体内容",
                value_text=content["description"] or None,
                unit="",
                context=content["description"],
                clarity=content["clarity"],
                verification_status=(
                    "needs_confirmation" if content_unclear else "model_extracted"
                ),
                source_type="model_extracted_document",
                source_id=f"{recognition_id}:content",
                now=now,
            )
            if content_unclear:
                needs_confirmation = True
                unclear_text = "；".join(content["unclear_parts"]) or "整体内容无法可靠读取"
                connection.execute(
                    """INSERT INTO clarification_requests
                       (clarification_id, observation_id, reason, question,
                        alternatives_json, status, answered_revision_id, created_at, answered_at)
                       VALUES (?, ?, ?, ?, '[]', 'pending', NULL, ?, NULL)""",
                    (
                        str(uuid4()),
                        content_observation,
                        unclear_text,
                        "请补充或更正图片中没有看清的整体内容",
                        now,
                    ),
                )
            connection.execute(
                """UPDATE recognition_runs SET status = 'succeeded', result_json = ?,
                   completed_at = ? WHERE recognition_id = ?""",
                (json.dumps(normalized, ensure_ascii=False), now, recognition_id),
            )
            connection.execute(
                """UPDATE medical_entries SET status = ?, version = version + 1
                   WHERE entry_id = ?""",
                (
                    "needs_confirmation" if needs_confirmation else "ready",
                    run["entry_id"],
                ),
            )
            self._bump_patient(connection, run["patient_id"], now)
        return self.entry(run["entry_id"])

    def fail_recognition(self, recognition_id: str, error_code: str) -> None:
        code = _clean_text(error_code, "错误类型", 80)
        now = utc_now()
        with self._transaction() as connection:
            run = connection.execute(
                """SELECT r.*, a.entry_id FROM recognition_runs r
                   JOIN source_assets a ON a.asset_id = r.asset_id
                   WHERE recognition_id = ?""",
                (recognition_id,),
            ).fetchone()
            if run is None or run["status"] != "running":
                return
            connection.execute(
                """UPDATE recognition_runs SET status = 'failed', completed_at = ?, error_code = ?
                   WHERE recognition_id = ?""",
                (now, code, recognition_id),
            )
            connection.execute(
                "UPDATE medical_entries SET status = 'recognition_failed' WHERE entry_id = ?",
                (run["entry_id"],),
            )

    def add_measurement(
        self,
        patient_id: str,
        *,
        kind: Any,
        value_text: Any,
        unit: Any,
        measured_at: Any,
        measurement_context: Any,
    ) -> dict[str, Any]:
        measurement_kind = _clean_text(kind, "测量项目", 60)
        try:
            spec = kind_spec(measurement_kind)
            normalized = normalize_measurement(
                measurement_kind, _clean_text(value_text, "测量数值", 40), unit
            )
        except MeasurementKindError as exc:
            raise PatientRecordError(str(exc)) from None
        if measurement_kind == "blood_glucose":
            allowed_contexts = GLUCOSE_MEASUREMENT_CONTEXTS
        else:
            allowed_contexts = GENERIC_MEASUREMENT_CONTEXTS
        if measurement_context not in allowed_contexts:
            raise PatientRecordError("请选择有效的测量场景")
        measured = _time(measured_at, "测量时间", required=True)
        entry_id = str(uuid4())
        now = utc_now()
        # Store the reported value/unit verbatim (alias-resolved unit);
        # canonical values are derived at read time, never written back
        # over what the patient entered or confirmed.
        value = normalized["reported_value"]
        normalized_unit = normalized["reported_unit"]
        if measurement_kind == "blood_pressure":
            # Dual-value kinds have no single numeric column: the pair
            # lives in value_text ("120/80") and numeric_value stays NULL.
            numeric: str | None = None
        else:
            numeric = str(Decimal(value))
        with self._transaction() as connection:
            self._require_patient(connection, patient_id)
            connection.execute(
                """INSERT INTO medical_entries
                   (entry_id, patient_id, entry_type, title, measured_at, reported_at,
                    uploaded_at, status, version, deleted_at)
                   VALUES (?, ?, 'manual_measurement', ?, ?, NULL, ?, 'ready', 1, NULL)""",
                (entry_id, patient_id, f"{spec['display_name']}自测", measured, now),
            )
            entry = connection.execute(
                "SELECT * FROM medical_entries WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            observation_id = self._insert_observation(
                connection,
                entry=entry,
                kind=measurement_kind,
                display_name=spec["display_name"],
                value_text=value,
                unit=normalized_unit,
                context=(
                    f"{_MEASUREMENT_CONTEXT_LABELS[measurement_context]}"
                    f"{spec['display_name']} {value} {normalized_unit}"
                ),
                clarity=normalized["clarity"],
                verification_status="patient_confirmed",
                source_type="patient_manual",
                source_id=entry_id,
                now=now,
            )
            connection.execute(
                """UPDATE observations SET numeric_value = ?, measurement_context = ?
                   WHERE observation_id = ?""",
                (numeric, measurement_context, observation_id),
            )
            self._bump_patient(connection, patient_id, now)
        detail = self.entry(entry_id)
        # Out-of-range values are still saved; the warning travels with
        # the receipt so the frontend can show it without blocking.
        detail["warnings"] = list(normalized["warnings"])
        return detail

    def _observation(self, connection: sqlite3.Connection, observation_id: str) -> sqlite3.Row:
        row = connection.execute(
            """SELECT o.*, e.patient_id, e.version AS entry_version, e.deleted_at
               FROM observations o JOIN medical_entries e ON e.entry_id = o.entry_id
               WHERE o.observation_id = ? AND e.deleted_at IS NULL""",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise PatientRecordError("医疗数据不存在")
        return row

    def revise_observation(
        self,
        observation_id: str,
        *,
        value_text: Any,
        unit: Any,
        expected_revision_id: Any,
        reason: str = "correction",
    ) -> dict[str, Any]:
        value = _clean_text(value_text, "修改内容", 300)
        normalized_unit = _clean_text(unit or "", "单位", 40, required=False)
        expected = _clean_text(expected_revision_id, "数据版本", 80)
        if reason not in {"correction", "confirmation"}:
            raise PatientRecordError("修改原因无效")
        now = utc_now()
        with self._transaction() as connection:
            current = self._observation(connection, observation_id)
            if current["current_revision_id"] != expected:
                raise PatientRecordConflict("这项数据已经变化，请刷新后再修改")
            revision_id = str(uuid4())
            verification = "patient_confirmed" if reason == "confirmation" else "patient_corrected"
            connection.execute(
                """INSERT INTO observation_revisions
                   (revision_id, observation_id, previous_revision_id, value_text, unit,
                    context, author_type, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'patient', ?, ?)""",
                (
                    revision_id,
                    observation_id,
                    current["current_revision_id"],
                    value,
                    normalized_unit,
                    current["context"],
                    reason,
                    now,
                ),
            )
            numeric = None
            clarity = "clear"
            if current["kind"] in KNOWN_KINDS:
                # Patient corrections of measurements re-validate through
                # the kind registry (blood-pressure pairs included); other
                # observation kinds keep their previous no-numeric path.
                try:
                    normalized = normalize_measurement(
                        current["kind"], value, normalized_unit
                    )
                except MeasurementKindError as exc:
                    raise PatientRecordError(str(exc)) from None
                clarity = normalized["clarity"]
                if current["kind"] != "blood_pressure":
                    numeric = str(Decimal(normalized["reported_value"]))
                value = normalized["reported_value"]
                normalized_unit = normalized["reported_unit"]
            connection.execute(
                """UPDATE observations SET value_text = ?, numeric_value = ?, unit = ?,
                   clarity = ?, verification_status = ?, current_revision_id = ?, updated_at = ?
                   WHERE observation_id = ?""",
                (
                    value,
                    numeric,
                    normalized_unit,
                    clarity,
                    verification,
                    revision_id,
                    now,
                    observation_id,
                ),
            )
            connection.execute(
                """UPDATE clarification_requests SET status = 'answered', answered_revision_id = ?,
                   answered_at = ? WHERE observation_id = ? AND status = 'pending'""",
                (revision_id, now, observation_id),
            )
            pending = connection.execute(
                """SELECT COUNT(*) FROM clarification_requests c
                   JOIN observations o ON o.observation_id = c.observation_id
                   WHERE o.entry_id = ? AND c.status = 'pending'""",
                (current["entry_id"],),
            ).fetchone()[0]
            connection.execute(
                """UPDATE medical_entries SET status = ?, version = version + 1
                   WHERE entry_id = ?""",
                ("needs_confirmation" if pending else "ready", current["entry_id"]),
            )
            self._bump_patient(connection, current["patient_id"], now)
        return self.entry(current["entry_id"])

    def answer_clarification(
        self,
        clarification_id: str,
        *,
        value_text: Any,
        unit: Any,
        expected_revision_id: Any,
    ) -> dict[str, Any]:
        with self._read() as connection:
            row = connection.execute(
                """SELECT c.*, o.current_revision_id FROM clarification_requests c
                   JOIN observations o ON o.observation_id = c.observation_id
                   WHERE c.clarification_id = ?""",
                (clarification_id,),
            ).fetchone()
        if row is None or row["status"] != "pending":
            raise PatientRecordConflict("这项核对已经完成或失效")
        if row["current_revision_id"] != expected_revision_id:
            raise PatientRecordConflict("这项数据已经变化，请刷新后再核对")
        return self.revise_observation(
            row["observation_id"],
            value_text=value_text,
            unit=unit,
            expected_revision_id=expected_revision_id,
            reason="correction",
        )

    def reset_recognition_orphan(self, asset_id: str) -> dict[str, Any]:
        """Reset an entry stranded in ``recognizing`` (P3d restart recovery).

        Only applies when the entry is actually stuck in recognizing
        (e.g. the process died mid-job and its in-memory job record is
        gone). Running recognition runs for the asset are marked
        failed with ``orphan_reset``; entries in any other status are
        left untouched and ``reset`` is False.
        """
        with self._transaction() as connection:
            row = self._asset_row(connection, asset_id)
            entry_id = row["entry_id"]
            if row["entry_status"] != "recognizing":
                return {"reset": False, "entry_id": entry_id}
            connection.execute(
                """UPDATE recognition_runs SET status = 'failed',
                   completed_at = ?, error_code = 'orphan_reset'
                   WHERE asset_id = ? AND status = 'running'""",
                (utc_now(), asset_id),
            )
            connection.execute(
                "UPDATE medical_entries SET status = 'recognition_failed' "
                "WHERE entry_id = ?",
                (entry_id,),
            )
            return {"reset": True, "entry_id": entry_id}

    def save_interpretation(
        self,
        patient_id: str,
        entry_id: str,
        model: str,
        model_version: str,
        input_revision_ids: list[str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one report-interpretation run (P3a).

        Grading itself lives in report_interpretation.py; this only
        stores the sheet. ``input_revision_ids`` are the current
        revision ids the sheet was graded from, so a later revision
        can never silently reuse a stale sheet.
        """
        from uuid import uuid4 as _uuid4

        analysis_id = str(_uuid4())
        now = utc_now()
        with self._transaction() as connection:
            self._require_patient(connection, patient_id)
            version_row = connection.execute(
                "SELECT version FROM patient_profiles WHERE patient_id = ?",
                (patient_id,),
            ).fetchone()
            entry = self._require_entry(connection, entry_id)
            if entry["patient_id"] != patient_id:
                raise PatientRecordError("该条目不属于该患者档案")
            connection.execute(
                """INSERT INTO analysis_runs
                   (analysis_id, patient_id, patient_version,
                    input_observation_ids, calculation_receipts,
                    review_mode, status, result_json, created_at,
                    completed_at, entry_id, model, model_version)
                   VALUES (?, ?, ?, ?, '[]', 'report_interpretation',
                           'ready', ?, ?, ?, ?, ?, ?)""",
                (
                    analysis_id,
                    patient_id,
                    version_row["version"] if version_row is not None else 1,
                    json.dumps(list(input_revision_ids), ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    now,
                    now,
                    entry_id,
                    model,
                    model_version,
                ),
            )
        return {"analysis_id": analysis_id, "status": "ready"}

    def latest_interpretation(self, entry_id: str) -> dict[str, Any] | None:
        """Return the newest ready interpretation sheet for an entry."""
        with self._read() as connection:
            self._require_entry(connection, entry_id)
            row = connection.execute(
                """SELECT * FROM analysis_runs
                   WHERE entry_id = ? AND status = 'ready'
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (entry_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["input_observation_ids"] = json.loads(
                item.get("input_observation_ids") or "[]"
            )
        except ValueError:
            item["input_observation_ids"] = []
        try:
            item["result"] = (
                json.loads(item["result_json"]) if item.get("result_json") else None
            )
        except ValueError:
            item["result"] = None
        item.pop("result_json", None)
        item.pop("calculation_receipts", None)
        return item
