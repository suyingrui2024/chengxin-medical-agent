"""Persistent, patient-visible longitudinal records for the desktop prototype."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from .extensions.glucose import convert_glucose
from .medical_images import InspectedImage, validate_recognition


SCHEMA_VERSION = 1
MEASUREMENT_CONTEXTS = {
    "fasting",
    "before_meal",
    "after_meal_1h",
    "after_meal_2h",
    "bedtime",
    "random",
    "unknown",
}


class PatientRecordError(ValueError):
    pass


class PatientRecordConflict(PatientRecordError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_patient_data_root() -> Path:
    override = os.environ.get("MEDAGENT_PATIENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Chengxin" / "patient-data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Chengxin" / "patient-data"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "chengxin" / "patient-data"


def _clean_text(value: Any, name: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise PatientRecordError(f"{name}格式错误")
    text = " ".join(value.split())
    if (required and not text) or len(text) > maximum:
        raise PatientRecordError(f"{name}格式错误")
    return text


def _time(value: Any, name: str, *, required: bool = False) -> str | None:
    if value in {None, ""} and not required:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise PatientRecordError(f"{name}格式错误")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PatientRecordError(f"{name}格式错误") from None
    if parsed.tzinfo is None:
        raise PatientRecordError(f"{name}必须包含时区")
    return parsed.isoformat().replace("+00:00", "Z")


class PatientRecordStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else default_patient_data_root()
        self.root = self.root.expanduser().resolve()
        self.assets = self.root / "assets"
        self.database = self.root / "patients.sqlite3"
        self.lock = RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets.mkdir(mode=0o700, exist_ok=True)
        try:
            self.root.chmod(0o700)
            self.assets.chmod(0o700)
        except OSError:
            pass
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self.lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS patient_profiles (
                        patient_id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        schema_version INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS medical_entries (
                        entry_id TEXT PRIMARY KEY,
                        patient_id TEXT NOT NULL REFERENCES patient_profiles(patient_id),
                        entry_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        measured_at TEXT,
                        reported_at TEXT,
                        uploaded_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        deleted_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_entries_patient_time
                        ON medical_entries(patient_id, measured_at, uploaded_at);
                    CREATE TABLE IF NOT EXISTS source_assets (
                        asset_id TEXT PRIMARY KEY,
                        entry_id TEXT NOT NULL REFERENCES medical_entries(entry_id),
                        relative_path TEXT NOT NULL UNIQUE,
                        original_filename TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        byte_size INTEGER NOT NULL,
                        width INTEGER NOT NULL,
                        height INTEGER NOT NULL,
                        page_count INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        deleted_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS recognition_runs (
                        recognition_id TEXT PRIMARY KEY,
                        asset_id TEXT NOT NULL REFERENCES source_assets(asset_id),
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        contract_version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT,
                        error_code TEXT
                    );
                    CREATE TABLE IF NOT EXISTS observations (
                        observation_id TEXT PRIMARY KEY,
                        entry_id TEXT NOT NULL REFERENCES medical_entries(entry_id),
                        kind TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        value_text TEXT,
                        numeric_value TEXT,
                        unit TEXT NOT NULL DEFAULT '',
                        context TEXT NOT NULL DEFAULT '',
                        measurement_context TEXT,
                        clarity TEXT NOT NULL,
                        verification_status TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        measured_at TEXT,
                        current_revision_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_observations_entry
                        ON observations(entry_id, created_at);
                    CREATE TABLE IF NOT EXISTS observation_revisions (
                        revision_id TEXT PRIMARY KEY,
                        observation_id TEXT NOT NULL REFERENCES observations(observation_id),
                        previous_revision_id TEXT,
                        value_text TEXT,
                        unit TEXT NOT NULL DEFAULT '',
                        context TEXT NOT NULL DEFAULT '',
                        author_type TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS clarification_requests (
                        clarification_id TEXT PRIMARY KEY,
                        observation_id TEXT NOT NULL REFERENCES observations(observation_id),
                        reason TEXT NOT NULL,
                        question TEXT NOT NULL,
                        alternatives_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        answered_revision_id TEXT,
                        created_at TEXT NOT NULL,
                        answered_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_clarifications_observation
                        ON clarification_requests(observation_id, status);
                    CREATE TABLE IF NOT EXISTS analysis_runs (
                        analysis_id TEXT PRIMARY KEY,
                        patient_id TEXT NOT NULL REFERENCES patient_profiles(patient_id),
                        patient_version INTEGER NOT NULL,
                        input_observation_ids TEXT NOT NULL,
                        calculation_receipts TEXT NOT NULL,
                        review_mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT
                    );
                    """
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            finally:
                connection.close()
        try:
            self.database.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_patient(self, display_name: str = "我的健康档案") -> dict[str, Any]:
        name = _clean_text(display_name, "档案名称", 80)
        patient_id = str(uuid4())
        now = utc_now()
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO patient_profiles VALUES (?, ?, 1, ?, ?, ?)",
                (patient_id, name, SCHEMA_VERSION, now, now),
            )
        return self.patient(patient_id)

    def ensure_default_patient(self) -> dict[str, Any]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM patient_profiles ORDER BY created_at LIMIT 1"
            ).fetchone()
        return dict(row) if row else self.create_patient()

    def patient(self, patient_id: str) -> dict[str, Any]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM patient_profiles WHERE patient_id = ?", (patient_id,)
            ).fetchone()
        if row is None:
            raise PatientRecordError("患者档案不存在")
        return dict(row)

    def list_patients(self) -> list[dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                """SELECT p.*,
                    (SELECT COUNT(*) FROM medical_entries e
                     WHERE e.patient_id = p.patient_id AND e.deleted_at IS NULL) AS entry_count
                   FROM patient_profiles p ORDER BY p.created_at"""
            ).fetchall()
        return [dict(row) for row in rows]

    def _require_patient(self, connection: sqlite3.Connection, patient_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM patient_profiles WHERE patient_id = ?", (patient_id,)
        ).fetchone() is None:
            raise PatientRecordError("患者档案不存在")

    def _require_entry(self, connection: sqlite3.Connection, entry_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM medical_entries WHERE entry_id = ? AND deleted_at IS NULL",
            (entry_id,),
        ).fetchone()
        if row is None:
            raise PatientRecordError("医疗记录不存在")
        return row

    @staticmethod
    def _bump_patient(connection: sqlite3.Connection, patient_id: str, now: str) -> None:
        connection.execute(
            "UPDATE patient_profiles SET version = version + 1, updated_at = ? WHERE patient_id = ?",
            (now, patient_id),
        )

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
        if measurement_kind != "blood_glucose":
            raise PatientRecordError("第一版只支持手动记录血糖")
        value = _clean_text(value_text, "测量数值", 40)
        try:
            numeric = Decimal(value)
        except InvalidOperation:
            raise PatientRecordError("血糖数值格式错误") from None
        if not numeric.is_finite() or numeric < 0 or len(value) > 20:
            raise PatientRecordError("血糖数值格式错误")
        normalized_unit = _clean_text(unit, "血糖单位", 20)
        if normalized_unit not in {"mmol/L", "mg/dL"}:
            raise PatientRecordError("血糖单位只支持 mmol/L 或 mg/dL")
        if measurement_context not in MEASUREMENT_CONTEXTS:
            raise PatientRecordError("请选择有效的血糖测量场景")
        measured = _time(measured_at, "测量时间", required=True)
        entry_id = str(uuid4())
        now = utc_now()
        context_labels = {
            "fasting": "空腹",
            "before_meal": "餐前",
            "after_meal_1h": "餐后 1 小时",
            "after_meal_2h": "餐后 2 小时",
            "bedtime": "睡前",
            "random": "随机",
            "unknown": "场景暂不确定",
        }
        with self._transaction() as connection:
            self._require_patient(connection, patient_id)
            connection.execute(
                """INSERT INTO medical_entries
                   (entry_id, patient_id, entry_type, title, measured_at, reported_at,
                    uploaded_at, status, version, deleted_at)
                   VALUES (?, ?, 'manual_measurement', '血糖自测', ?, NULL, ?, 'ready', 1, NULL)""",
                (entry_id, patient_id, measured, now),
            )
            entry = connection.execute(
                "SELECT * FROM medical_entries WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            observation_id = self._insert_observation(
                connection,
                entry=entry,
                kind=measurement_kind,
                display_name="血糖",
                value_text=value,
                unit=normalized_unit,
                context=f"{context_labels[measurement_context]}血糖 {value} {normalized_unit}",
                clarity="clear",
                verification_status="patient_confirmed",
                source_type="patient_manual",
                source_id=entry_id,
                now=now,
            )
            connection.execute(
                """UPDATE observations SET numeric_value = ?, measurement_context = ?
                   WHERE observation_id = ?""",
                (str(numeric), measurement_context, observation_id),
            )
            self._bump_patient(connection, patient_id, now)
        return self.entry(entry_id)

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
            if current["kind"] == "blood_glucose":
                try:
                    parsed = Decimal(value)
                except InvalidOperation:
                    raise PatientRecordError("血糖数值格式错误") from None
                if not parsed.is_finite() or parsed < 0:
                    raise PatientRecordError("血糖数值格式错误")
                if normalized_unit not in {"mmol/L", "mg/dL"}:
                    raise PatientRecordError("血糖单位只支持 mmol/L 或 mg/dL")
                numeric = str(parsed)
            connection.execute(
                """UPDATE observations SET value_text = ?, numeric_value = ?, unit = ?,
                   clarity = 'clear', verification_status = ?, current_revision_id = ?, updated_at = ?
                   WHERE observation_id = ?""",
                (
                    value,
                    numeric,
                    normalized_unit,
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

    def timeline(self, patient_id: str) -> dict[str, Any]:
        patient = self.patient(patient_id)
        with self._read() as connection:
            rows = connection.execute(
                """SELECT e.*,
                    (SELECT COUNT(*) FROM observations o WHERE o.entry_id = e.entry_id) AS observation_count,
                    (SELECT COUNT(*) FROM clarification_requests c
                     JOIN observations o ON o.observation_id = c.observation_id
                     WHERE o.entry_id = e.entry_id AND c.status = 'pending') AS pending_count,
                    (SELECT asset_id FROM source_assets a
                     WHERE a.entry_id = e.entry_id AND a.deleted_at IS NULL LIMIT 1) AS asset_id
                   FROM medical_entries e
                   WHERE e.patient_id = ? AND e.deleted_at IS NULL
                   ORDER BY COALESCE(e.measured_at, e.reported_at, e.uploaded_at) DESC,
                            e.uploaded_at DESC""",
                (patient_id,),
            ).fetchall()
        return {"patient": patient, "entries": [dict(row) for row in rows]}

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
