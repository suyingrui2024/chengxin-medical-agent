"""Host-owned workspace index, outside all model-runtime mounts. No credentials."""

import fcntl
import json
from .execution_observations import validate_observation
from .execution_limits import DEFAULT_TIME_BUDGET_SECONDS, time_budget
import math
import os
from pathlib import Path
import stat
from uuid import UUID, uuid4
from .execution_capabilities import validate_binding, validate_receipt


FORMAT = "execution-workspace/v1"
DSH_VERSION = "0.2.0-rc.2"
STATUSES = {
    "created",
    "starting",
    "running",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
}


class WorkspaceStoreError(ValueError):
    pass


class WorkspaceOccupied(WorkspaceStoreError):
    pass


def identifier(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise WorkspaceStoreError("工作区标识无效") from None
    return value


class WorkspaceStore:
    def __init__(self, root):
        self.root = Path(root)

    def _directory(self, create=False):
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        base = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if create:
                try:
                    os.mkdir(".index", mode=0o700, dir_fd=base)
                except FileExistsError:
                    pass
            return os.open(
                ".index", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base
            )
        finally:
            os.close(base)

    def acquire(self, identity):
        identity = identifier(identity)
        directory = self._directory(create=True)
        try:
            fd = os.open(
                identity + ".lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise WorkspaceStoreError("工作区锁文件无效")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                os.close(fd)
                raise WorkspaceOccupied("工作区正在另一个窗口使用，请先关闭原窗口或工作区") from None
            except Exception:
                os.close(fd)
                raise
        finally:
            os.close(directory)

    def save(self, identity, record):
        identity = identifier(identity)
        data = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > 2_000_000:
            raise WorkspaceStoreError("工作区记录过大，未能保存")
        directory = self._directory(create=True)
        temporary = "." + identity + "." + uuid4().hex + ".tmp"
        try:
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(
                    temporary,
                    identity + ".json",
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                )
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory)

    def read(self, identity):
        identity = identifier(identity)
        directory = self._directory()
        try:
            fd = os.open(
                identity + ".json",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise WorkspaceStoreError("工作区记录不是普通文件")
                data = bytearray()
                while len(data) <= 2_000_000:
                    part = os.read(fd, min(65536, 2_000_001 - len(data)))
                    if not part:
                        break
                    data.extend(part)
                if len(data) > 2_000_000:
                    raise WorkspaceStoreError("工作区记录过大，拒绝读取")
            finally:
                os.close(fd)
        finally:
            os.close(directory)
        try:
            record = json.loads(data)
            self.validate(record, identity)
            return record
        except (ValueError, TypeError, KeyError, OverflowError):
            raise WorkspaceStoreError("工作区记录损坏或版本不兼容；未创建替代会话") from None

    @staticmethod
    def validate(record, identity):
        if not isinstance(record, dict):
            raise ValueError()
        if "project_path" in record:
            path = record["project_path"]
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or path == "/"
                or len(path.encode("utf-8")) > 4096
                or os.path.normpath(path) != path
                or any(ord(char) < 32 for char in path)
            ):
                raise ValueError()
        observations = record.get("observations", [])
        if not isinstance(observations, list) or len(observations) > 128:
            raise ValueError()
        for observation in observations:
            validate_observation(observation)
            if not any(
                turn["id"] == observation["turn_id"] for turn in record["turns"]
            ):
                raise ValueError()
        if (
            record["format"] != FORMAT
            or record["id"] != identity
            or record["dsh_version"] != DSH_VERSION
            or record["status"] not in STATUSES
        ):
            raise ValueError()
        for name in ["created_at", "updated_at"]:
            if (
                type(record[name]) not in {int, float}
                or not math.isfinite(record[name])
                or record[name] <= 0
            ):
                raise ValueError()
        for name in ["directory_identity", "state_identity"]:
            if (
                not isinstance(record[name], list)
                or len(record[name]) != 2
                or any(type(v) is not int or v < 0 for v in record[name])
            ):
                raise ValueError()
        if (
            not isinstance(record["route_sha256"], str)
            or len(record["route_sha256"]) != 64
            or any(v not in "0123456789abcdef" for v in record["route_sha256"])
        ):
            raise ValueError()
        for name, limit in [("model", 256), ("provider_host", 256), ("title", 80)]:
            if not isinstance(record[name], str) or len(record[name]) > limit:
                raise ValueError()
        if (
            type(record["has_runtime_history"]) is not bool
            or type(record["turn_count"]) is not int
            or record["turn_count"] < 0
        ):
            raise ValueError()
        if not isinstance(record["turns"], list) or len(record["turns"]) > 32:
            raise ValueError()
        if (
            record["turn_count"] < len(record["turns"])
            or record["status"] == "completed"
            and not record["has_runtime_history"]
        ):
            raise ValueError()
        if record.get("error") is not None and (
            not isinstance(record["error"], str) or len(record["error"]) > 512
        ):
            raise ValueError()
        validate_binding(record.get("capability_binding"))
        calculations = record.get("calculations", [])
        if not isinstance(calculations, list) or len(calculations) > 32:
            raise ValueError()
        for result in calculations:
            validate_receipt(result)
            identifier(result["execution_id"])
            identifier(result["turn_id"])
            identifier(result["evidence"]["source_turn_id"])
            if result["workspace_id"] != identity or not any(
                t["id"] == result["turn_id"] for t in record["turns"]
            ):
                raise ValueError()
            binding = record.get("capability_binding")
            if not binding or any(
                result[key] != binding[key]
                for key in ["registry_sha256", "implementation_sha256"]
            ):
                raise ValueError()
        if len({result["execution_id"] for result in calculations}) != len(
            calculations
        ):
            raise ValueError()
        for turn in record["turns"]:
            if (
                "changes_recorded" in turn
                and type(turn["changes_recorded"]) is not bool
            ):
                raise ValueError()
            time_budget(turn.get("time_budget_seconds", DEFAULT_TIME_BUDGET_SECONDS))
            identifier(turn["id"])
            if (
                turn["status"] not in STATUSES
                or not isinstance(turn["text"], str)
                or len(turn["text"]) > 16000
                or not isinstance(turn.get("final_response", ""), str)
                or len(turn.get("final_response", "")) > 24000
            ):
                raise ValueError()
            if type(turn["max_calls"]) is not int or not 1 <= turn["max_calls"] <= 64:
                raise ValueError()
            if (
                type(turn.get("model_calls", 0)) is not int
                or not 0 <= turn.get("model_calls", 0) <= 200
            ):
                raise ValueError()

    def catalog(self):
        try:
            directory = self._directory()
        except FileNotFoundError:
            return []
        try:
            names = [
                entry.name
                for entry in os.scandir(directory)
                if entry.name.endswith(".json")
            ][:200]
        finally:
            os.close(directory)
        records = []
        for name in names:
            try:
                identity = identifier(name[:-5])
            except WorkspaceStoreError:
                continue
            try:
                record = self.read(identity)
                records.append(
                    {
                        k: record[k]
                        for k in ["id", "title", "model", "provider_host", "updated_at"]
                    }
                    | {"status": "saved", "project_path": record.get("project_path")}
                )
            except (WorkspaceStoreError, OSError):
                records.append(
                    {
                        "id": identity,
                        "title": "记录无法读取",
                        "status": "unavailable",
                        "updated_at": 0,
                    }
                )
        return sorted(records, key=lambda r: r["updated_at"], reverse=True)
