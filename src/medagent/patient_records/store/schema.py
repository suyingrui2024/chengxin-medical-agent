"""Mixin: schema for PatientRecordStore."""
from ._preamble import *  # noqa: F401,F403


class StoreSchema:
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
                        birth_year INTEGER,
                        sex TEXT,
                        height_cm REAL,
                        weight_kg REAL,
                        chronic_conditions TEXT NOT NULL DEFAULT '[]',
                        allergies TEXT NOT NULL DEFAULT '[]',
                        medications TEXT NOT NULL DEFAULT '[]',
                        surgical_history TEXT,
                        birth_year_month TEXT,
                        archived_at TEXT,
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
                # Forward migration v1 -> v2: a database created by the
                # single-profile release has the six-column table above
                # without the baseline columns. ADD COLUMN is enough
                # (new columns are all nullable or defaulted), so existing
                # profiles, entries and observations are untouched.
                existing_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(patient_profiles)"
                    ).fetchall()
                }
                for column, definition in (
                    ("birth_year", "INTEGER"),
                    ("sex", "TEXT"),
                    ("height_cm", "REAL"),
                    ("weight_kg", "REAL"),
                    ("chronic_conditions", "TEXT NOT NULL DEFAULT '[]'"),
                    ("allergies", "TEXT NOT NULL DEFAULT '[]'"),
                    ("medications", "TEXT NOT NULL DEFAULT '[]'"),
                    ("surgical_history", "TEXT"),
                    ("birth_year_month", "TEXT"),
                    ("archived_at", "TEXT"),
                ):
                    if column not in existing_columns:
                        connection.execute(
                            f"ALTER TABLE patient_profiles ADD COLUMN {column} {definition}"
                        )
                # P3a forward migration: analysis_runs was created
                # without the interpretation columns (entry/model/
                # model_version); old libraries gain them without
                # touching any existing run, entry or observation.
                analysis_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(analysis_runs)"
                    ).fetchall()
                }
                for column, definition in (
                    ("entry_id", "TEXT"),
                    ("model", "TEXT"),
                    ("model_version", "TEXT"),
                ):
                    if column not in analysis_columns:
                        connection.execute(
                            f"ALTER TABLE analysis_runs ADD COLUMN {column} {definition}"
                        )
                connection.execute(
                    "UPDATE patient_profiles SET schema_version = ? "
                    "WHERE schema_version < ?",
                    (SCHEMA_VERSION, SCHEMA_VERSION),
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

    def create_patient(
        self, display_name: str = "我的健康档案", baseline: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create a new profile. Multi-profile is intentional (P2d):
        the first profile created is the self profile; later calls add
        family members. Use ensure_default_patient() for the self one."""
        fields = validate_baseline_patch({"display_name": display_name, **(baseline or {})})
        patient_id = str(uuid4())
        now = utc_now()
        columns = ["patient_id", "display_name", "version", "schema_version"]
        values: list[Any] = [patient_id, fields["display_name"], 1, SCHEMA_VERSION]
        for key in BASELINE_FIELDS:
            columns.append(key)
            raw = fields.get(key)
            values.append(
                json.dumps(raw, ensure_ascii=False)
                if key in {"chronic_conditions", "allergies", "medications"}
                else raw
            )
        columns += ["created_at", "updated_at"]
        values += [now, now]
        placeholders = ", ".join("?" * len(columns))
        with self._transaction() as connection:
            connection.execute(
                f"INSERT INTO patient_profiles ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                values,
            )
        return self.patient(patient_id)

    def add_member(
        self, display_name: str, baseline: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Add a family member profile; same baseline shape as self."""
        return self.create_patient(display_name, baseline)

    def ensure_default_patient(self) -> dict[str, Any]:
        """Return the self profile: the earliest non-archived profile.

        Existing single-profile data is untouched: with one profile it
        is that profile, so migration costs nothing.
        """
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM patient_profiles WHERE archived_at IS NULL "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
        return _profile_dict(row) if row else self.create_patient()

    def list_patients(self, include_archived: bool = True) -> list[dict[str, Any]]:
        with self._read() as connection:
            query = "SELECT * FROM patient_profiles"
            if not include_archived:
                query += " WHERE archived_at IS NULL"
            query += " ORDER BY created_at"
            rows = connection.execute(query).fetchall()
        return [_profile_dict(row) for row in rows]

    def patient(self, patient_id: str) -> dict[str, Any]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM patient_profiles WHERE patient_id = ?", (patient_id,)
            ).fetchone()
        if row is None:
            raise PatientRecordError("患者档案不存在")
        return _profile_dict(row)

    def update_patient(
        self, patient_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """Partial baseline update, patient-entered only.

        Validation runs before any write and the update is a single
        transaction, so an invalid patch leaves stored data untouched.
        Consultation facts are never written here: no caller passes
        them in, and there is no fact-to-baseline path in this module.
        """
        fields = validate_baseline_patch(patch)
        if not fields:
            raise PatientRecordError("基线信息格式错误")
        now = utc_now()
        assignments = []
        values: list[Any] = []
        for key, raw in fields.items():
            assignments.append(f"{key} = ?")
            values.append(
                json.dumps(raw, ensure_ascii=False)
                if key in {"chronic_conditions", "allergies", "medications"}
                else raw
            )
        assignments.append("version = version + 1")
        assignments.append("updated_at = ?")
        values += [now, patient_id]
        with self._transaction() as connection:
            self._require_patient(connection, patient_id)
            connection.execute(
                f"UPDATE patient_profiles SET {', '.join(assignments)} "
                "WHERE patient_id = ?",
                values,
            )
        return self.patient(patient_id)

    def archive_patient(self, patient_id: str) -> dict[str, Any]:
        """Soft-archive a member: flagged, never hard-deleted, and no
        longer eligible as the default profile.

        The current default (self) profile cannot be archived: archiving
        it would let ensure_default_patient() silently promote a family
        member to "self", rebinding every default-scoped read/write
        (measurements, trends, uploads) to the wrong person. Refusing
        here (400 at the API layer) keeps the default stable; a caller
        that needs to retire the self profile must do so explicitly
        through a different, deliberate path (none exists yet).
        """
        now = utc_now()
        with self._transaction() as connection:
            self._require_patient(connection, patient_id)
            default = connection.execute(
                "SELECT patient_id FROM patient_profiles "
                "WHERE archived_at IS NULL ORDER BY created_at LIMIT 1"
            ).fetchone()
            if default is not None and default[0] == patient_id:
                raise PatientRecordError("默认档案不能归档")
            connection.execute(
                "UPDATE patient_profiles SET archived_at = ?, version = version + 1, "
                "updated_at = ? WHERE patient_id = ? AND archived_at IS NULL",
                (now, now, patient_id),
            )
        return self.patient(patient_id)

    def _require_patient(self, connection: sqlite3.Connection, patient_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM patient_profiles WHERE patient_id = ?", (patient_id,)
        ).fetchone() is None:
            raise PatientRecordError("患者档案不存在")

    @staticmethod
    def _bump_patient(connection: sqlite3.Connection, patient_id: str, now: str) -> None:
        connection.execute(
            "UPDATE patient_profiles SET version = version + 1, updated_at = ? WHERE patient_id = ?",
            (now, patient_id),
        )
