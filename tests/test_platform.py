"""The platform decisions, exercised on both branches.

Before `infra/platform.py` existed, these answers were inline
`os.name == "nt"` expressions inside business modules. The Windows side of
each one was unreachable from a POSIX checkout, so it was covered by nothing
outside CI — and `45 if os.name == "nt" else 25` had no name to hang a comment
on.

Flipping the module's `_WINDOWS` / `_MACOS` flags makes both branches reachable
here. That is the whole point of extracting them: one patch point instead of
an `os.name` global.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from medagent.infra import platform


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(platform, "_WINDOWS", True)
    monkeypatch.setattr(platform, "_MACOS", False)


@pytest.fixture
def as_macos(monkeypatch):
    monkeypatch.setattr(platform, "_WINDOWS", False)
    monkeypatch.setattr(platform, "_MACOS", True)


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(platform, "_WINDOWS", False)
    monkeypatch.setattr(platform, "_MACOS", False)


# --- predicates ------------------------------------------------------------


def test_the_predicates_follow_the_flags(as_windows):
    assert platform.is_windows() is True
    assert platform.is_macos() is False


def test_macos_is_recognised(as_macos):
    assert platform.is_windows() is False
    assert platform.is_macos() is True


# --- startup budget --------------------------------------------------------


def test_windows_gets_the_longer_startup_budget(as_windows):
    """The job container plus the Node worker starts slower than bubblewrap."""
    assert platform.startup_timeout_seconds() == 45


@pytest.mark.parametrize("fixture_name", ["as_macos", "as_linux"])
def test_posix_gets_the_shorter_startup_budget(fixture_name, request):
    request.getfixturevalue(fixture_name)
    assert platform.startup_timeout_seconds() == 25


# --- isolation label -------------------------------------------------------


def test_the_snapshot_names_the_isolation_mechanism(as_windows):
    assert platform.isolation_kind() == "windows-processcontainer"


def test_posix_reports_bubblewrap(as_linux):
    assert platform.isolation_kind() == "bubblewrap-filesystem"


# --- can the worker run at all --------------------------------------------


def test_without_node_the_worker_cannot_start(monkeypatch):
    monkeypatch.setattr(platform.shutil, "which", lambda name: None)
    assert platform.supports_sandbox() is False


def test_windows_needs_only_node(as_windows, monkeypatch):
    monkeypatch.setattr(platform.shutil, "which", lambda name: "/usr/bin/node")
    assert platform.supports_sandbox() is True


def test_posix_needs_bubblewrap_as_well(as_linux, monkeypatch):
    monkeypatch.setattr(
        platform.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None
    )
    assert platform.supports_sandbox() is False


def test_posix_with_both_is_ready(as_linux, monkeypatch):
    monkeypatch.setattr(platform.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert platform.supports_sandbox() is True


# --- patient data ----------------------------------------------------------


def test_the_environment_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDAGENT_PATIENT_DATA_DIR", str(tmp_path / "custom"))
    assert platform.patient_data_root() == tmp_path / "custom"


def test_windows_uses_local_app_data(as_windows, monkeypatch, tmp_path):
    monkeypatch.delenv("MEDAGENT_PATIENT_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert platform.patient_data_root() == tmp_path / "Local" / "Chengxin" / "patient-data"


def test_macos_uses_application_support(as_macos, monkeypatch):
    monkeypatch.delenv("MEDAGENT_PATIENT_DATA_DIR", raising=False)
    root = platform.patient_data_root()
    assert root.parts[-3:] == ("Chengxin", "patient-data") or root.name == "patient-data"
    assert "Application Support" in root.parts


def test_linux_honours_xdg_data_home(as_linux, monkeypatch, tmp_path):
    monkeypatch.delenv("MEDAGENT_PATIENT_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert platform.patient_data_root() == tmp_path / "share" / "chengxin" / "patient-data"


# --- protected roots -------------------------------------------------------


def test_posix_protects_the_system_directories(as_linux):
    roots = platform.protected_roots()
    assert Path("/usr") in roots
    assert Path("/etc") in roots
    # Windows-only names must not leak into a POSIX list.
    assert not any(str(root).startswith("C:") for root in roots)


def test_windows_protects_its_own_system_directories(as_windows, monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    roots = platform.protected_roots()
    assert (tmp_path / "Windows").resolve() in roots
    assert (tmp_path / "Program Files").resolve() in roots
    assert Path("/usr") not in roots


def test_a_missing_windows_variable_is_skipped_not_crashed(as_windows, monkeypatch):
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    assert platform.protected_roots() == []
