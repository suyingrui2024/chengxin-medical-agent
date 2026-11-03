"""Reading files inside the workspace without following anything out of it.

Every read goes through an owned directory descriptor and resolves each
component of the path relative to it, so a symlink, a replaced directory or a
path that climbs out of the workspace cannot widen what the caller can reach.
The size limit and the revision check are the two other guards: the first
bounds how much a single file can cost, the second notices a file changing
while it is being read.
"""
from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath

from ..infra.platform_fs import os
from .errors import ExecutionError


def open_workspace_directory(root, identity=None):
    """Refuse missing, linked or replaced workspace roots before reading data."""
    directory = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(directory)
        if identity is not None and (info.st_dev, info.st_ino) != identity:
            raise ExecutionError("工作目录已被替换，请恢复原目录或新建工作区")
        return directory
    except (OSError, ExecutionError):
        if directory is not None:
            os.close(directory)
        raise ExecutionError("工作目录不存在、已被替换或不可读取；请恢复原目录或新建工作区") from None

def file_revision(info):
    return f"{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}"

def safe_bytes(root: Path, relative: str, limit=128_000, root_identity=None):
    """Open every component relative to an owned directory fd, never a symlink."""
    if not isinstance(relative, str):
        raise ExecutionError("文件路径无效")
    try:
        relative.encode("utf-8")
    except UnicodeEncodeError:
        raise ExecutionError("文件名称须为有效UTF-8") from None
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or any(p in {"..", ".", ""} for p in relative.split("/"))
        or "\\" in relative
        # A NUL byte is rejected here rather than left to `open`, which raises a
        # bare ValueError. Every other refusal from this guard is an
        # ExecutionError, and callers are entitled to rely on that.
        or "\x00" in relative
    ):
        raise ExecutionError("文件路径无效")
    directory = open_workspace_directory(root, root_identity)
    try:
        for component in path.parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        fd = os.open(
            path.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ExecutionError("仅允许读取工作区普通文件")
            if info.st_size > limit:
                raise ExecutionError("文件过大，请在本机工作目录查看")
            chunks, count = [], 0
            while count <= limit:
                chunk = os.read(fd, min(64 * 1024, limit + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
            data = b"".join(chunks)
            after = os.fstat(fd)
            if file_revision(after) != file_revision(info):
                raise ExecutionError("文件读取期间发生变化，请重新读取")
        finally:
            os.close(fd)
    except OSError:
        raise ExecutionError("文件不存在、为链接或不可读取") from None
    finally:
        os.close(directory)
    if len(data) > limit:
        raise ExecutionError("文件过大，请在本机工作目录查看")
    return data, file_revision(info)

def safe_file(root: Path, relative: str, limit=128_000, root_identity=None):
    data, _ = safe_bytes(root, relative, limit, root_identity)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ExecutionError("当前预览支持 UTF-8 文本文件") from None
    return {"path": relative, "text": text, "size": len(data)}


__all__ = [
    "file_revision",
    "open_workspace_directory",
    "safe_bytes",
    "safe_file",
]
