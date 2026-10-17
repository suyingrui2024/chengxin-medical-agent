"""One-directory, bounded, identity-aware listings; never follow workspace links."""
import base64
import hashlib
import json
from .platform_fs import os
from pathlib import PurePosixPath
import stat
import time


class DirectoryChanged(ValueError):
    pass


def path_parts(path):
    if not isinstance(path, str) or len(path) > 4096:
        raise ValueError("目录路径无效")
    if path in {"", "."}:
        return ()
    if (
        path.startswith("/")
        or "\\" in path
        or any(p in {"", ".", ".."} for p in path.split("/"))
    ):
        raise ValueError("仅接受工作区内的相对目录路径")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("目录名称须为有效UTF-8") from None
    parts = PurePosixPath(path).parts
    if len(parts) > 64:
        raise ValueError("目录层级超过浏览范围，可用文件相对路径直接预览")
    return parts


def entry_key(item):
    return (
        {"directory": 0, "file": 1, "link": 2, "unavailable": 3, "other": 3}[
            item["kind"]
        ],
        item["name"],
    )


def directory_page(
    root_fd,
    path="",
    cursor=None,
    limit=100,
    include_hidden=False,
    *,
    max_entries=10000,
    max_seconds=1.0,
):
    parts = path_parts(path)
    path = "/".join(parts)
    if (
        type(limit) is not int
        or not 1 <= limit <= 200
        or type(include_hidden) is not bool
    ):
        raise ValueError("目录分页参数无效")
    previous = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
                raise ValueError()
            decoded = base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            )
            previous = json.loads(decoded)
            if (
                set(previous) != {"revision", "after"}
                or not isinstance(previous["revision"], str)
                or len(previous["revision"]) != 64
                or not isinstance(previous["after"], list)
                or len(previous["after"]) != 2
                or type(previous["after"][0]) is not int
                or not 0 <= previous["after"][0] <= 3
                or not isinstance(previous["after"][1], str)
                or len(previous["after"][1]) > 255
            ):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("目录翻页标记无效，请重新读取目录") from None
    directory = os.dup(root_fd)
    entries, reasons, scanned = [], set(), 0
    started = time.monotonic()
    try:
        for component in parts:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        identity = os.fstat(directory)
        with os.scandir(directory) as values:
            for entry in values:
                if scanned >= max_entries:
                    reasons.add("entry_limit")
                    break
                if time.monotonic() - started > max_seconds:
                    reasons.add("time_limit")
                    break
                scanned += 1
                if not include_hidden and (
                    entry.name.startswith(".")
                    or entry.name in {"node_modules", "__pycache__"}
                ):
                    continue
                try:
                    entry.name.encode("utf-8")
                except UnicodeEncodeError:
                    reasons.add("unsupported_name")
                    continue
                item = {
                    "name": entry.name,
                    "path": f"{path}/{entry.name}" if path else entry.name,
                    "size": None,
                    "revision": None,
                }
                try:
                    info = entry.stat(follow_symlinks=False)
                    item["kind"] = (
                        "directory"
                        if stat.S_ISDIR(info.st_mode)
                        else "file"
                        if stat.S_ISREG(info.st_mode)
                        else "link"
                        if stat.S_ISLNK(info.st_mode)
                        else "other"
                    )
                    if item["kind"] == "file":
                        item.update(
                            size=info.st_size,
                            revision=f"{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}",
                        )
                except OSError:
                    item["kind"] = "unavailable"
                    reasons.add("unreadable_entry")
                entries.append(item)
    except OSError:
        raise ValueError("目录不存在、为链接或不可读取") from None
    finally:
        os.close(directory)
    entries.sort(key=entry_key)
    revision = hashlib.sha256(
        json.dumps(
            [
                identity.st_dev,
                identity.st_ino,
                path,
                include_hidden,
                sorted(reasons),
                [entry_key(e) for e in entries],
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if previous and previous["revision"] != revision:
        raise DirectoryChanged("目录条目已变化，请从第一页重新读取")
    remaining = [
        e for e in entries if not previous or entry_key(e) > tuple(previous["after"])
    ]
    page = remaining[:limit]
    next_cursor = None
    if len(remaining) > limit:
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {"revision": revision, "after": entry_key(page[-1])},
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
    return {
        "schema_version": "execution-directory/v1",
        "path": path,
        "parent": "/".join(parts[:-1]) if parts else None,
        "entries": page,
        "next_cursor": next_cursor,
        "scan_complete": not reasons,
        "total_entries": len(entries) if not reasons else None,
        "scanned_entries": scanned,
        "reasons": sorted(reasons),
        "include_hidden": include_hidden,
        "revision": revision,
    }
