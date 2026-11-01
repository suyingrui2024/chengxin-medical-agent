"""Platform differences, named.

The heavy lifting is already isolated — ``infra/platform_fs`` owns fd-relative
filesystem calls, ``infra/processes`` owns process trees, ``infra/windows_process``
owns the Windows job container. What was left was the small stuff: seven
``os.name == "nt"`` tests scattered through business modules, including a
startup timeout written as ``45 if os.name == "nt" else 25`` inside an
expression, with no name and no comment.

Each decision below answers one question a caller actually has. Naming them
makes the platform split greppable and lets a test patch a single function
instead of an ``os.name`` global.
"""
import os
import shutil
import sys
from pathlib import Path

_WINDOWS = os.name == "nt"
_MACOS = sys.platform == "darwin"

__all__ = [
    "is_macos",
    "is_windows",
    "isolation_kind",
    "patient_data_root",
    "protected_roots",
    "startup_timeout_seconds",
    "supports_sandbox",
]


def is_windows() -> bool:
    return _WINDOWS


def is_macos() -> bool:
    return _MACOS


def startup_timeout_seconds() -> int:
    """How long to wait for the execution worker to report ``ready``.

    Windows is given nearly twice as long: starting the job container, the
    Python child and the Node worker there is measurably slower than the
    bubblewrap path, and a timeout that is too tight turns a slow machine into
    a failed turn.
    """
    return 45 if _WINDOWS else 25


def isolation_kind() -> str:
    """The isolation mechanism this host uses, as reported in the snapshot."""
    return "windows-processcontainer" if _WINDOWS else "bubblewrap-filesystem"


def supports_sandbox() -> bool:
    """Whether this host can run the execution worker at all.

    POSIX needs bubblewrap *and* Node; Windows needs Node, because the job
    container supplies the isolation instead of bubblewrap.
    """
    if not shutil.which("node"):
        return False
    return _WINDOWS or bool(shutil.which("bwrap"))


def patient_data_root() -> Path:
    """Where patient records live, per platform convention.

    ``MEDAGENT_PATIENT_DATA_DIR`` overrides it outright, which is what the tests
    and the desktop shell use.
    """
    override = os.environ.get("MEDAGENT_PATIENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if _WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Chengxin" / "patient-data"
    if _MACOS:
        return Path.home() / "Library" / "Application Support" / "Chengxin" / "patient-data"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "chengxin" / "patient-data"


def protected_roots() -> list[Path]:
    """System directories an execution workspace may never point at.

    A workspace root inside one of these would let a task read or write the
    host's own system files, so the guard rejects them by name.
    """
    if _WINDOWS:
        return [
            Path(os.environ[name]).resolve()
            for name in (
                "SystemRoot",
                "ProgramFiles",
                "ProgramFiles(x86)",
                "APPDATA",
                "LOCALAPPDATA",
            )
            if os.environ.get(name)
        ]
    return [
        Path(name)
        for name in (
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
        )
    ]
