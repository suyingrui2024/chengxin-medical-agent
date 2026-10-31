"""Mixin: queries for PatientRecordStore."""
from ._preamble import *  # noqa: F401,F403


class StoreQueries:
    def timeline(self, patient_id: str) -> dict[str, Any]:
        patient = self.patient(patient_id)
        with self._read() as connection:
            rows = connection.execute(
                """SELECT e.*,
                    (SELECT COUNT(*) FROM observations o WHERE o.entry_id = e.entry_id) AS observation_count,
                    (SELECT COUNT(*) FROM clarification_requests c
                     JOIN observations o ON o.observation_id = c.observation_id
                     WHERE o.entry_id = e.entry_id AND c.status = 'pending') AS pending_count,
                    (SELECT COUNT(*) FROM observations o
                     WHERE o.entry_id = e.entry_id
                       AND o.verification_status IN ('model_extracted', 'needs_confirmation'))
                       AS review_count,
                    (SELECT asset_id FROM source_assets a
                     WHERE a.entry_id = e.entry_id AND a.deleted_at IS NULL LIMIT 1) AS asset_id
                   FROM medical_entries e
                   WHERE e.patient_id = ? AND e.deleted_at IS NULL
                   ORDER BY COALESCE(e.measured_at, e.reported_at, e.uploaded_at) DESC,
                            e.uploaded_at DESC""",
                (patient_id,),
            ).fetchall()
            observations = connection.execute(
                """SELECT o.entry_id, o.observation_id, o.kind, o.display_name,
                          o.value_text, o.unit, o.measurement_context,
                          o.verification_status, o.current_revision_id
                   FROM observations o
                   JOIN medical_entries e ON e.entry_id = o.entry_id
                   WHERE e.patient_id = ? AND e.deleted_at IS NULL
                     AND o.kind != 'document_content'
                   ORDER BY o.created_at, o.observation_id""",
                (patient_id,),
            ).fetchall()
            interpreted = {
                row["entry_id"]
                for row in connection.execute(
                    "SELECT entry_id FROM analysis_runs "
                    "WHERE patient_id = ? AND status = 'ready' GROUP BY entry_id",
                    (patient_id,),
                ).fetchall()
            }
        highlights: dict[str, list[dict[str, Any]]] = {}
        for observation in observations:
            highlights.setdefault(observation["entry_id"], []).append(dict(observation))
        entries = []
        for row in rows:
            item = dict(row)
            # P3a: timeline badge source of truth; the frontend only
            # renders the badge when this flag is true, never guesses.
            item["has_interpretation"] = item["entry_id"] in interpreted
            item["highlights"] = highlights.get(item["entry_id"], [])[:3]
            item["confirmed_count"] = sum(
                observation["verification_status"]
                in {"patient_confirmed", "patient_corrected"}
                for observation in highlights.get(item["entry_id"], [])
            )
            entries.append(item)
        return {"patient": patient, "entries": entries}

    def entry_id_for_asset(self, asset_id: str) -> str:
        """Return the entry owning an asset (P3d async recognition).

        The recognitions route only carries the asset id; the job
        store needs the entry id, and guessing it from anywhere else
        would be wrong. Raises PatientRecordError for unknown assets.
        """
        with self._read() as connection:
            row = self._asset_row(connection, asset_id)
        return row["entry_id"]

    def shareable_entry(self, entry_id: str) -> dict[str, Any]:
        """Return current patient-verified values that may enter a consultation."""
        detail = self.entry(entry_id)
        verified = []
        for observation in detail["observations"]:
            if observation["verification_status"] not in {
                "patient_confirmed",
                "patient_corrected",
            }:
                continue
            if not observation.get("value_text"):
                continue
            verified.append(
                {
                    key: observation.get(key)
                    for key in (
                        "observation_id",
                        "current_revision_id",
                        "kind",
                        "display_name",
                        "value_text",
                        "unit",
                        "measurement_context",
                        "measured_at",
                        "verification_status",
                    )
                }
            )
        return {"entry": detail["entry"], "observations": verified}

    def glucose_trend(self, patient_id: str) -> dict[str, Any]:
        self.patient(patient_id)
        with self._read() as connection:
            rows = connection.execute(
                """SELECT o.observation_id, o.value_text, o.unit, o.measured_at,
                          o.measurement_context, o.current_revision_id
                   FROM observations o
                   JOIN medical_entries e ON e.entry_id = o.entry_id
                   WHERE e.patient_id = ? AND e.deleted_at IS NULL
                     AND o.kind = 'blood_glucose'
                     AND o.verification_status IN ('patient_confirmed', 'patient_corrected')
                     AND o.measured_at IS NOT NULL
                   ORDER BY o.measured_at, o.created_at""",
                (patient_id,),
            ).fetchall()
        points = []
        for row in rows:
            receipt = convert_glucose(
                {
                    "value": row["value_text"],
                    "from_unit": row["unit"],
                    "to_unit": "mmol/L",
                }
            )
            points.append(
                {
                    "observation_id": row["observation_id"],
                    "revision_id": row["current_revision_id"],
                    "measured_at": row["measured_at"],
                    "measurement_context": row["measurement_context"],
                    "reported_value": row["value_text"],
                    "reported_unit": row["unit"],
                    "normalized_value": receipt["value"],
                    "normalized_unit": "mmol/L",
                    "conversion": {
                        "capability": "calculate.glucose",
                        "version": "0.1.0",
                        "formula": receipt["formula"],
                        "approximate": receipt["approximate"],
                    },
                }
            )
        if not points:
            return {
                "schema": "blood-glucose-trend/v1",
                "patient_id": patient_id,
                "count": 0,
                "points": [],
                "summary": None,
                "interpretation": "没有已确认的血糖自测数据。",
            }
        values = [Decimal(point["normalized_value"]) for point in points]
        average = sum(values) / Decimal(len(values))

        def rendered(value: Decimal) -> str:
            output = format(value.quantize(Decimal("0.0001")), "f")
            return output.rstrip("0").rstrip(".")

        change = values[-1] - values[0] if len(values) > 1 else Decimal(0)
        return {
            "schema": "blood-glucose-trend/v1",
            "patient_id": patient_id,
            "count": len(points),
            "points": points,
            "summary": {
                "earliest": points[0],
                "latest": points[-1],
                "minimum": rendered(min(values)),
                "maximum": rendered(max(values)),
                "average": rendered(average),
                "change_from_first": rendered(change),
                "unit": "mmol/L",
            },
            "interpretation": "仅计算已确认读数的描述性变化；未判断高低、诊断或用药。",
        }

    def measurement_trend(self, kind: str, patient_id: str) -> dict[str, Any]:
        """Per-kind trend with the same shape as glucose_trend.

        Scalar kinds mirror the glucose shape exactly (points with a
        canonical normalized_value, summary with earliest/latest/
        minimum/maximum/average/change_from_first). Blood pressure is
        the one structural exception: its summary carries systolic and
        diastolic sub-summaries instead of a single average, and its
        points carry the "120/80" pair plus both components.
        Unparseable stored rows are skipped, never fatal to the page.
        """
        try:
            spec = kind_spec(kind)
        except MeasurementKindError as exc:
            raise PatientRecordError(str(exc)) from None
        self.patient(patient_id)
        with self._read() as connection:
            rows = connection.execute(
                """SELECT o.observation_id, o.value_text, o.unit, o.measured_at,
                          o.measurement_context, o.current_revision_id
                   FROM observations o
                   JOIN medical_entries e ON e.entry_id = o.entry_id
                   WHERE e.patient_id = ? AND e.deleted_at IS NULL
                     AND o.kind = ?
                     AND o.verification_status IN ('patient_confirmed', 'patient_corrected')
                     AND o.measured_at IS NOT NULL
                   ORDER BY o.measured_at, o.created_at""",
                (patient_id, kind),
            ).fetchall()
        display = spec["display_name"]
        canonical = spec["canonical_unit"]
        if kind == "blood_pressure":
            points = []
            samples = []
            for row in rows:
                try:
                    normalized = normalize_measurement(
                        kind, row["value_text"], row["unit"]
                    )
                except MeasurementKindError:
                    continue
                samples.append(normalized)
                points.append(
                    {
                        "observation_id": row["observation_id"],
                        "revision_id": row["current_revision_id"],
                        "measured_at": row["measured_at"],
                        "measurement_context": row["measurement_context"],
                        "reported_value": normalized["reported_value"],
                        "reported_unit": normalized["reported_unit"],
                        "systolic": _render_decimal(
                            normalized["values"]["systolic"]
                        ),
                        "diastolic": _render_decimal(
                            normalized["values"]["diastolic"]
                        ),
                        "normalized_unit": canonical,
                    }
                )
            if not points:
                return {
                    "schema": f"{kind}-trend/v1",
                    "patient_id": patient_id,
                    "count": 0,
                    "points": [],
                    "summary": None,
                    "interpretation": f"没有已确认的{display}自测数据。",
                }
            stats = trend_summary(kind, samples)
            summary = {
                "earliest": points[0],
                "latest": points[-1],
                "systolic": {
                    "minimum": stats["series"]["systolic"]["minimum"],
                    "maximum": stats["series"]["systolic"]["maximum"],
                    "average": stats["series"]["systolic"]["mean"],
                    "latest": stats["series"]["systolic"]["latest"],
                },
                "diastolic": {
                    "minimum": stats["series"]["diastolic"]["minimum"],
                    "maximum": stats["series"]["diastolic"]["maximum"],
                    "average": stats["series"]["diastolic"]["mean"],
                    "latest": stats["series"]["diastolic"]["latest"],
                },
                "unit": canonical,
            }
            return {
                "schema": f"{kind}-trend/v1",
                "patient_id": patient_id,
                "count": len(points),
                "points": points,
                "summary": summary,
                "interpretation": "仅计算已确认读数的描述性变化；未判断高低、诊断或用药。",
            }
        points = []
        samples = []
        for row in rows:
            try:
                normalized = normalize_measurement(
                    kind, row["value_text"], row["unit"]
                )
            except MeasurementKindError:
                continue
            samples.append(normalized)
            point: dict[str, Any] = {
                "observation_id": row["observation_id"],
                "revision_id": row["current_revision_id"],
                "measured_at": row["measured_at"],
                "measurement_context": row["measurement_context"],
                "reported_value": row["value_text"],
                "reported_unit": row["unit"],
                "normalized_value": _render_decimal(normalized["values"]["value"]),
                "normalized_unit": canonical,
            }
            if kind == "blood_glucose":
                receipt = convert_glucose(
                    {
                        "value": row["value_text"],
                        "from_unit": row["unit"],
                        "to_unit": "mmol/L",
                    }
                )
                point["normalized_value"] = receipt["value"]
                point["conversion"] = {
                    "capability": "calculate.glucose",
                    "version": "0.1.0",
                    "formula": receipt["formula"],
                    "approximate": receipt["approximate"],
                }
            points.append(point)
        if not points:
            return {
                "schema": f"{kind}-trend/v1",
                "patient_id": patient_id,
                "count": 0,
                "points": [],
                "summary": None,
                "interpretation": f"没有已确认的{display}自测数据。",
            }
        values = [sample["values"]["value"] for sample in samples]
        change = values[-1] - values[0] if len(values) > 1 else Decimal(0)
        return {
            "schema": f"{kind}-trend/v1",
            "patient_id": patient_id,
            "count": len(points),
            "points": points,
            "summary": {
                "earliest": points[0],
                "latest": points[-1],
                "minimum": _render_decimal(min(values)),
                "maximum": _render_decimal(max(values)),
                "average": _render_decimal(sum(values) / Decimal(len(values))),
                "change_from_first": _render_decimal(change),
                "unit": canonical,
            },
            "interpretation": "仅计算已确认读数的描述性变化；未判断高低、诊断或用药。",
        }

    def recent_confirmed_measurements(
        self, patient_id: str, days: int = 7
    ) -> list[dict[str, Any]]:
        """Confirmed scalar measurements inside the window, canonicalized.

        Feed for build_baseline_projection: only patient-confirmed /
        patient-corrected rows, only the last ``days`` days, values
        already converted to the kind's canonical unit. Blood pressure
        is skipped here — a slash pair cannot be summarized as one
        float, and the projection's summarizer drops it anyway.
        """
        self.patient(patient_id)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        with self._read() as connection:
            rows = connection.execute(
                """SELECT o.kind, o.value_text, o.unit, o.measured_at,
                          o.verification_status
                   FROM observations o
                   JOIN medical_entries e ON e.entry_id = o.entry_id
                   WHERE e.patient_id = ? AND e.deleted_at IS NULL
                     AND o.kind IN ('blood_glucose', 'blood_pressure', 'temperature',
                                    'weight', 'blood_oxygen')
                     AND o.verification_status IN ('patient_confirmed', 'patient_corrected')
                     AND o.measured_at IS NOT NULL
                   ORDER BY o.measured_at, o.created_at""",
                (patient_id,),
            ).fetchall()
        results = []
        for row in rows:
            if row["kind"] == "blood_pressure":
                continue
            try:
                measured_at = datetime.fromisoformat(
                    row["measured_at"].replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if measured_at.tzinfo is None:
                measured_at = measured_at.replace(tzinfo=timezone.utc)
            if measured_at < cutoff or measured_at > now:
                continue
            try:
                normalized = normalize_measurement(
                    row["kind"], row["value_text"], row["unit"]
                )
            except MeasurementKindError:
                continue
            results.append(
                {
                    "kind": row["kind"],
                    "value": float(normalized["values"]["value"]),
                    "unit": normalized["canonical_unit"],
                    "measured_at": row["measured_at"],
                    "verification_status": row["verification_status"],
                }
            )
        return results

    def entry(self, entry_id: str) -> dict[str, Any]:
        with self._read() as connection:
            entry = self._require_entry(connection, entry_id)
            assets = connection.execute(
                """SELECT asset_id, original_filename, sha256, mime_type, byte_size,
                          width, height, page_count, created_at
                   FROM source_assets WHERE entry_id = ? AND deleted_at IS NULL""",
                (entry_id,),
            ).fetchall()
            observations = connection.execute(
                "SELECT * FROM observations WHERE entry_id = ? ORDER BY created_at, observation_id",
                (entry_id,),
            ).fetchall()
            observation_values = []
            for observation in observations:
                item = dict(observation)
                item["revisions"] = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT * FROM observation_revisions WHERE observation_id = ?
                           ORDER BY created_at, revision_id""",
                        (observation["observation_id"],),
                    ).fetchall()
                ]
                item["clarifications"] = []
                for row in connection.execute(
                    """SELECT * FROM clarification_requests WHERE observation_id = ?
                       ORDER BY created_at, clarification_id""",
                    (observation["observation_id"],),
                ).fetchall():
                    clarification = dict(row)
                    clarification["alternatives"] = json.loads(
                        clarification.pop("alternatives_json")
                    )
                    item["clarifications"].append(clarification)
                observation_values.append(item)
            recognition_rows = connection.execute(
                """SELECT r.* FROM recognition_runs r
                   JOIN source_assets a ON a.asset_id = r.asset_id
                   WHERE a.entry_id = ? ORDER BY r.created_at""",
                (entry_id,),
            ).fetchall()
            recognitions = []
            for row in recognition_rows:
                item = dict(row)
                item["result"] = json.loads(item.pop("result_json")) if item["result_json"] else None
                recognitions.append(item)
        return {
            "entry": dict(entry),
            "assets": [dict(row) for row in assets],
            "observations": observation_values,
            "recognitions": recognitions,
        }

    def delete_entry(self, entry_id: str, expected_version: Any) -> None:
        if not isinstance(expected_version, int) or expected_version < 1:
            raise PatientRecordError("记录版本无效")
        now = utc_now()
        paths: list[Path] = []
        with self._transaction() as connection:
            entry = self._require_entry(connection, entry_id)
            if entry["version"] != expected_version:
                raise PatientRecordConflict("这条记录已经变化，请刷新后再删除")
            assets = connection.execute(
                "SELECT relative_path FROM source_assets WHERE entry_id = ? AND deleted_at IS NULL",
                (entry_id,),
            ).fetchall()
            paths = [(self.root / row["relative_path"]).resolve() for row in assets]
            connection.execute(
                """DELETE FROM clarification_requests WHERE observation_id IN
                   (SELECT observation_id FROM observations WHERE entry_id = ?)""",
                (entry_id,),
            )
            connection.execute(
                """DELETE FROM observation_revisions WHERE observation_id IN
                   (SELECT observation_id FROM observations WHERE entry_id = ?)""",
                (entry_id,),
            )
            connection.execute(
                "DELETE FROM observations WHERE entry_id = ?", (entry_id,)
            )
            connection.execute(
                """DELETE FROM recognition_runs WHERE asset_id IN
                   (SELECT asset_id FROM source_assets WHERE entry_id = ?)""",
                (entry_id,),
            )
            self._bump_patient(connection, entry["patient_id"], now)
            connection.execute("DELETE FROM source_assets WHERE entry_id = ?", (entry_id,))
            connection.execute("DELETE FROM medical_entries WHERE entry_id = ?", (entry_id,))
        for path in paths:
            if path.parent == self.assets and not path.is_symlink():
                path.unlink(missing_ok=True)
