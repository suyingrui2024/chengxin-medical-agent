"""Validate explicitly selected local project roots before binding a runtime."""

import os
from pathlib import Path
import shutil
import sys


def project_path(value, storage_root, source_root):
    if not isinstance(value, (str, Path)):
        raise ValueError("请选择一个已有的项目文件夹")
    raw = str(value)
    if (
        not Path(raw).is_absolute()
        or len(raw.encode("utf-8")) > 4096
        or any(ord(char) < 32 for char in raw)
    ):
        raise ValueError("项目目录路径无效")
    candidate = Path(raw).resolve(strict=True)
    if not candidate.is_dir():
        raise ValueError("请选择文件夹，不能选择单个文件")
    # These contain the app's trusted code, credentials, SDK dependencies or
    # its private records. Neither they nor an enclosing folder may be mounted.
    home = Path.home().resolve()
    protected = [
        Path(source_root).resolve(),
        Path(storage_root).resolve(),
        Path(sys.prefix).resolve(),
    ]
    protected += [
        home / name
        for name in [".ssh", ".aws", ".codex", ".config", ".gnupg", ".local", ".volta"]
    ]
    node = shutil.which("node")
    if node:
        protected.append(Path(node).resolve().parent.parent)
    protected += [
        Path(name)
        for name in [
            "/usr",
            "/etc",
            "/proc",
            "/sys",
            "/dev",
            "/lib",
            "/lib64",
            "/bin",
            "/sbin",
            "/boot",
            "/run",
        ]
    ] if os.name != "nt" else [
        Path(os.environ[name]).resolve()
        for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "APPDATA", "LOCALAPPDATA")
        if os.environ.get(name)
    ]
    if candidate in {
        Path(candidate.anchor),
        Path("/home"),
        home,
        Path("/tmp"),
        Path("/var"),
        Path("/var/tmp"),
    } or any(
        candidate == path or candidate in path.parents or path in candidate.parents
        for path in protected
    ):
        raise ValueError("此目录包含系统、应用运行或私有资料，请选择独立的项目文件夹")
    if not os.access(candidate, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError("项目目录需要读取、写入和进入权限")
    return candidate
