"""Bounded workspace observations and host-owned baselines, independent of Git."""

from __future__ import annotations

import difflib
import hashlib
import json
import math
from .platform_fs import os
from pathlib import Path, PurePosixPath
import stat
import sys
import time
from uuid import uuid4

from .execution_store import identifier
from .execution_identity import decode_identity

FORMAT = "workspace-baseline/v1"
MAX_RECORD_BYTES = 4_000_000
IGNORED_DIRECTORIES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


class ChangeError(ValueError):
    pass


def watch_path_covers(parent, path):
    if parent == path:
        return True
    if parent == ".":
        suffix = path
    elif path.startswith(parent + "/"):
        suffix = path[len(parent) + 1 :]
    else:
        return False
    return not any(part in IGNORED_DIRECTORIES for part in suffix.split("/"))


def relative_path(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 1024
        or "\\" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(c) < 32 for c in value)
    ):
        raise ChangeError("文件路径无效")
    return PurePosixPath(value)


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def text_allowed(path):
    parts = PurePosixPath(path).parts
    return not any(
        p.lower().startswith(".env")
        or p.lower()
        in {".aws", ".ssh", "credentials", "secrets.json", ".benchmark_dependencies"}
        for p in parts
    ) and PurePosixPath(path).suffix.lower() not in {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".env",
    }


def capture(
    directory,
    identity,
    secret="",
    *,
    max_files=20000,
    max_bytes=256_000_000,
    max_file_bytes=32_000_000,
    paths=None,
    with_previews=True,
):
    from .execution import open_workspace_directory

    selected = paths or ["."]
    for path in selected:
        if path != ".":
            relative_path(path)
    root = open_workspace_directory(directory, identity)
    result = {
        "captured_at": time.time(),
        "complete": True,
        "content_complete": True,
        "entries": {},
        "issues": [],
        "ignored_directories": sorted(IGNORED_DIRECTORIES),
        "symlink_targets_followed": False,
        "empty_directories_tracked": False,
    }
    remaining = [max_bytes, 512_000, 3_500_000]
    stopped = [False]
    visited = [0]

    def issue(path, reason, *, listing=False):
        if listing:
            result["complete"] = False
        result["content_complete"] = False
        if len(result["issues"]) < 20:
            result["issues"].append({"path": path, "reason": reason})

    def walk(fd, prefix="", depth=0):
        if depth > 32:
            issue(prefix, "depth_limit", listing=True)
            return
        try:
            with os.scandir(fd) as iterator:
                for item in iterator:
                    if stopped[0]:
                        return
                    name = prefix + item.name
                    parts = name.split("/")
                    try:
                        ignored = [
                            "/".join(parts[: index + 1])
                            for index, part in enumerate(parts)
                            if part in IGNORED_DIRECTORIES
                            and (
                                index < len(parts) - 1
                                or item.is_dir(follow_symlinks=False)
                            )
                        ]
                    except OSError:
                        issue(name, "unreadable_or_changed", listing=True)
                        continue
                    effective = selected
                    if ignored:
                        boundary = ignored[-1]
                        effective = [
                            path
                            for path in selected
                            if path == boundary or path.startswith(boundary + "/")
                        ]
                    if not any(
                        path == "."
                        or name == path
                        or name.startswith(path + "/")
                        or path.startswith(name + "/")
                        for path in effective
                    ):
                        continue
                    visited[0] += 1
                    if visited[0] > 50000:
                        issue(prefix, "file_limit", listing=True)
                        stopped[0] = True
                        return
                    try:
                        relative_path(name)
                        info = item.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            child = os.open(
                                item.name,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd,
                            )
                            try:
                                walk(child, name + "/", depth + 1)
                            finally:
                                os.close(child)
                            continue
                        if len(result["entries"]) >= max_files:
                            issue(name, "file_limit", listing=True)
                            stopped[0] = True
                            return
                        entry = {
                            "kind": "file",
                            "mode": stat.S_IMODE(info.st_mode),
                            "size": info.st_size,
                            "sha256": None,
                            "text": None,
                        }
                        if stat.S_ISLNK(info.st_mode):
                            entry["kind"] = "symlink"
                            entry["sha256"] = hashlib.sha256(
                                os.fsencode(os.readlink(item.name, dir_fd=fd))
                            ).hexdigest()
                            if fingerprint(info) != fingerprint(
                                os.stat(item.name, dir_fd=fd, follow_symlinks=False)
                            ):
                                raise OSError("Link changed during read")
                        elif not stat.S_ISREG(info.st_mode):
                            entry["kind"] = "special"
                            issue(name, "special_file")
                        elif (
                            info.st_size > max_file_bytes or info.st_size > remaining[0]
                        ):
                            issue(name, "content_limit")
                        else:
                            opened = os.open(
                                item.name,
                                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=fd,
                            )
                            try:
                                before = os.fstat(opened)
                                if not stat.S_ISREG(before.st_mode) or fingerprint(
                                    before
                                ) != fingerprint(info):
                                    raise OSError("File changed before read")
                                digest = hashlib.sha256()
                                read = 0
                                while True:
                                    data = os.read(
                                        opened,
                                        min(
                                            1_000_000,
                                            max(
                                                1,
                                                min(max_file_bytes - read, remaining[0])
                                                + 1,
                                            ),
                                        ),
                                    )
                                    if not data:
                                        break
                                    read += len(data)
                                    remaining[0] -= len(data)
                                    if read > max_file_bytes or remaining[0] < 0:
                                        raise OSError("File exceeded observation bound")
                                    digest.update(data)
                                after = os.fstat(opened)
                                named = os.stat(
                                    item.name, dir_fd=fd, follow_symlinks=False
                                )
                                if fingerprint(before) != fingerprint(
                                    after
                                ) or fingerprint(after) != fingerprint(named):
                                    raise OSError("File changed during read")
                                entry["sha256"] = digest.hexdigest()
                            finally:
                                os.close(opened)
                        cost = len(
                            json.dumps({name: entry}, ensure_ascii=False).encode()
                        )
                        if cost > remaining[2]:
                            issue(name, "metadata_limit", listing=True)
                            stopped[0] = True
                            return
                        remaining[2] -= cost
                        result["entries"][name] = entry
                    except (OSError, ValueError, UnicodeError):
                        issue(
                            name if name.isascii() else "",
                            "unreadable_or_changed",
                            listing=True,
                        )
        except OSError:
            issue(prefix, "unreadable_directory", listing=True)

    def preview_priority(name):
        parts = PurePosixPath(name).parts
        docs = any(
            part in {"doc", "docs", "examples", "tests", "test"} for part in parts[:-1]
        )
        code = PurePosixPath(name).suffix.lower() in {
            ".py",
            ".js",
            ".mjs",
            ".cjs",
            ".ts",
            ".tsx",
            ".jsx",
            ".go",
            ".rs",
            ".c",
            ".h",
            ".cpp",
            ".java",
            ".sql",
        }
        return (
            3 if docs else 0 if parts[0] == "src" else 1 if code else 2,
            len(parts),
            name,
        )

    def previews():
        for name in sorted(result["entries"], key=preview_priority):
            entry = result["entries"][name]
            if (
                entry["kind"] != "file"
                or entry["sha256"] is None
                or entry["size"] > min(32768, remaining[0], remaining[1])
                or not text_allowed(name)
            ):
                continue
            parent = os.dup(root)
            try:
                parts = PurePosixPath(name).parts
                for part in parts[:-1]:
                    child = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent,
                    )
                    os.close(parent)
                    parent = child
                opened = os.open(
                    parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
                with os.fdopen(opened, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size > min(
                        32768, remaining[0]
                    ):
                        raise OSError("File changed before preview")
                    data = stream.read(min(32769, remaining[0] + 1))
                    remaining[0] -= len(data)
                    after = os.fstat(stream.fileno())
                named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                if (
                    fingerprint(before) != fingerprint(after)
                    or fingerprint(after) != fingerprint(named)
                    or hashlib.sha256(data).hexdigest() != entry["sha256"]
                ):
                    raise OSError("File changed during preview")
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if secret:
                    text = text.replace(secret, "[REDACTED]")
                text_bytes = len(text.encode())
                candidate = {**entry, "text": text}
                cost = len(json.dumps(candidate, ensure_ascii=False).encode()) - len(
                    json.dumps(entry, ensure_ascii=False).encode()
                )
                if (
                    text_bytes <= min(32768, remaining[1])
                    and cost <= remaining[2]
                    and "\x00" not in text
                ):
                    entry["text"] = text
                    remaining[1] -= text_bytes
                    remaining[2] -= cost
            except OSError:
                entry["sha256"] = None
                issue(name, "unreadable_or_changed")
            finally:
                os.close(parent)

    try:
        walk(root)
        if with_previews:
            previews()
        current = os.stat(directory, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != tuple(identity) or not stat.S_ISDIR(
            current.st_mode
        ):
            raise ChangeError("工作目录身份已改变，未采用其他目录")
    except OSError:
        raise ChangeError("工作目录不可读取") from None
    finally:
        os.close(root)
    return result


def compare(before, after, limit=200, offset=0):
    entries, counts = [], {
        name: 0 for name in ["added", "modified", "deleted", "unverified"]
    }
    for name in sorted(set(before["entries"]) | set(after["entries"])):
        old, new = before["entries"].get(name), after["entries"].get(name)
        if old is None:
            state = "added" if before["complete"] else "unverified"
        elif new is None:
            state = "deleted" if after["complete"] else "unverified"
        elif old["sha256"] is None or new["sha256"] is None:
            state = "unverified"
        elif (
            old["kind"] == new["kind"]
            and old["sha256"] == new["sha256"]
            and old["mode"] == new["mode"]
        ):
            continue
        else:
            state = "modified"
        counts[state] += 1
        entries.append(
            {
                "path": name,
                "state": state,
                "kind": (new or old)["kind"],
                "before_size": old["size"] if old else None,
                "after_size": new["size"] if new else None,
                "before_sha256": old["sha256"] if old else None,
                "after_sha256": new["sha256"] if new else None,
                "before_mode": old["mode"] if old else None,
                "after_mode": new["mode"] if new else None,
            }
        )
    entries.sort(key=lambda e: (e["state"] == "unverified", e["path"]))
    return {
        "counts": counts,
        "entries": entries[offset : offset + limit],
        "omitted": max(0, len(entries) - offset - limit),
        "offset": offset,
        "total_changes": len(entries),
        "next_offset": offset + limit if offset + limit < len(entries) else None,
        "baseline_complete": before["complete"],
        "current_complete": after["complete"],
        "baseline_content_complete": before["content_complete"],
        "current_content_complete": after["content_complete"],
        "issues": after["issues"],
        "baseline_issues": before["issues"],
        "symlink_targets_followed": False,
        "empty_directories_tracked": False,
        "ignored_directories": sorted(IGNORED_DIRECTORIES),
    }


class ChangeTracker:
    def __init__(self, directory, identity, store_root, workspace_id, secret=""):
        self.directory, self.identity = Path(directory), tuple(identity)
        self.root, self.id, self.secret = (
            Path(store_root),
            identifier(workspace_id),
            secret,
        )

    def _index(self, create=False):
        base = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in [".index", "changes", self.id]:
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=base)
                    except FileExistsError:
                        pass
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base
                )
                os.close(base)
                base = child
            return base
        except Exception:
            os.close(base)
            raise

    def begin(self, turn_id):
        turn_id = identifier(turn_id)
        observed = capture(self.directory, self.identity, self.secret)
        value = {
            "format": FORMAT,
            "workspace_id": self.id,
            "turn_id": turn_id,
            "root_identity": list(self.identity),
            **observed,
        }
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > MAX_RECORD_BYTES:
            raise ChangeError("变更基线超过保存上限")
        fd = self._index(True)
        temporary = "." + uuid4().hex + ".tmp"
        try:
            opened = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(opened, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, turn_id + ".json", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
            os.close(fd)
        return {
            "files": len(observed["entries"]),
            "complete": observed["complete"],
            "content_complete": observed["content_complete"],
        }

    def load(self, turn_id):
        turn_id = identifier(turn_id)
        directory = self._index()
        try:
            fd = os.open(
                turn_id + ".json",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ChangeError("变更基线不是普通文件")
                data = bytearray()
                while len(data) <= MAX_RECORD_BYTES:
                    block = os.read(fd, min(65536, MAX_RECORD_BYTES + 1 - len(data)))
                    if not block:
                        break
                    data.extend(block)
            finally:
                os.close(fd)
        finally:
            os.close(directory)
        if len(data) > MAX_RECORD_BYTES:
            raise ChangeError("变更基线超过读取上限")
        value = json.loads(data)
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "format",
                "workspace_id",
                "turn_id",
                "root_identity",
                "captured_at",
                "complete",
                "content_complete",
                "entries",
                "issues",
                "ignored_directories",
                "symlink_targets_followed",
                "empty_directories_tracked",
            }
            or value.get("format") != FORMAT
            or value.get("workspace_id") != self.id
            or value.get("turn_id") != turn_id
            or value.get("root_identity") != list(self.identity)
        ):
            raise ChangeError("变更基线身份不匹配")
        if (
            value.get("symlink_targets_followed") is not False
            or value.get("empty_directories_tracked") is not False
        ):
            raise ChangeError("变更基线策略不兼容")
        stamp = value.get("captured_at")
        if (
            type(stamp) not in {int, float}
            or not 0 < stamp <= 2**53 - 1
            or not math.isfinite(stamp)
            or value.get("ignored_directories") != sorted(IGNORED_DIRECTORIES)
        ):
            raise ChangeError("变更基线时间或扫描范围无效")
        if (
            type(value.get("complete")) is not bool
            or type(value.get("content_complete")) is not bool
            or not isinstance(value.get("entries"), dict)
            or len(value["entries"]) > 20000
        ):
            raise ChangeError("变更基线格式无效")
        if not isinstance(value.get("issues"), list) or len(value["issues"]) > 20:
            raise ChangeError("变更基线异常清单无效")
        for issue in value["issues"]:
            if (
                not isinstance(issue, dict)
                or set(issue) != {"path", "reason"}
                or not all(
                    isinstance(issue.get(k), str) and len(issue[k].encode()) <= 1024
                    for k in ["path", "reason"]
                )
            ):
                raise ChangeError("变更基线异常项无效")
        for name, entry in value["entries"].items():
            relative_path(name)
            if (
                not isinstance(entry, dict)
                or set(entry) != {"kind", "size", "sha256", "text", "mode"}
                or not isinstance(entry.get("kind"), str)
                or entry.get("kind") not in {"file", "symlink", "special"}
                or type(entry.get("size")) is not int
                or entry["size"] < 0
                or entry["size"] > 2**63 - 1
                or type(entry.get("mode")) is not int
                or not 0 <= entry["mode"] <= 0o7777
            ):
                raise ChangeError("变更基线文件信息无效")
            sha = entry.get("sha256")
            if sha is not None and (
                not isinstance(sha, str)
                or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)
            ):
                raise ChangeError("变更基线摘要无效")
            text = entry.get("text")
            if text is not None and (
                not isinstance(text, str) or len(text.encode()) > 32768
            ):
                raise ChangeError("变更基线预览无效")
        return value

    def discard(self, turn_id):
        turn_id = identifier(turn_id)
        directory = self._index()
        try:
            try:
                os.unlink(turn_id + ".json", dir_fd=directory)
            except FileNotFoundError:
                pass
        finally:
            os.close(directory)

    def prune(self, retained):
        retained = {identifier(value) for value in retained}
        directory = self._index()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not entry.name.endswith(".json"):
                        continue
                    try:
                        identity = identifier(entry.name[:-5])
                    except ValueError:
                        continue
                    if identity not in retained:
                        os.unlink(entry.name, dir_fd=directory)
        finally:
            os.close(directory)

    def observe(self, turn_id, path=None, *, limit=200, offset=0):
        if (
            type(limit) is not int
            or not 1 <= limit <= 200
            or type(offset) is not int
            or not 0 <= offset <= 40000
        ):
            raise ChangeError("变更分页参数无效")
        before = self.load(turn_id)
        after = capture(self.directory, self.identity, self.secret)
        result = {
            "schema": "workspace-changes/v1",
            "workspace_id": self.id,
            "turn_id": turn_id,
            "captured_at": before["captured_at"],
            "observed_at": after["captured_at"],
            **compare(before, after, limit, offset),
        }
        if path is not None:
            relative_path(path)
            old, new = before["entries"].get(path), after["entries"].get(path)
            detail = {"path": path, "available": False, "diff": "", "truncated": False}
            if (
                (old is None or old["text"] is not None)
                and (new is None or new["text"] is not None)
                and (old is not None or new is not None)
            ):
                previous, current = (
                    old["text"] if old else "",
                    new["text"] if new else "",
                )
                if self.secret:
                    previous, current = previous.replace(
                        self.secret, "[REDACTED]"
                    ), current.replace(self.secret, "[REDACTED]")
                lines = []
                for line in difflib.unified_diff(
                    previous.splitlines(True),
                    current.splitlines(True),
                    fromfile="a/" + path,
                    tofile="b/" + path,
                ):
                    lines.append(
                        line
                        if line.endswith("\n")
                        else line + "\n\\ No newline at end of file\n"
                    )
                    if len(lines) >= 400 or sum(len(x) for x in lines) > 16000:
                        detail["truncated"] = True
                        break
                detail.update(available=True, diff="".join(lines)[:18000])
            result["detail"] = detail
        return result


def main():
    try:
        data = json.loads(sys.stdin.buffer.read(32001))
        tracker = ChangeTracker(
            data["directory"],
            decode_identity(data["identity"]),
            data["store_root"],
            data["workspace_id"],
            data.get("secret", ""),
        )
        result = tracker.observe(
            data["turn_id"],
            data.get("path"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
        )
        reply = {"ok": True, "result": result}
    except (ValueError, OSError, KeyError, TypeError):
        reply = {
            "ok": False,
            "error": {
                "code": "workspace_changes_unavailable",
                "message": "无法读取本轮变更基线或工作目录。",
            },
        }
    sys.stdout.write(json.dumps(reply, ensure_ascii=False, allow_nan=False) + "\n")
