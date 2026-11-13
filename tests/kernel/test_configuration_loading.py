"""Startup precedence and shared parser behavior, without real model requests."""

from pathlib import Path

import pytest

from medagent.infra.env_config import read_configuration, read_partial


def configuration(path: Path, *, model="fixture", mode="compatible"):
    path.write_text(
        f"MEDAGENT_BACKEND={mode}\nMEDAGENT_LLM_MODEL={model}\n"
        "MEDAGENT_LLM_API_KEY='test-secret'\n"
        "MEDAGENT_LLM_BASE_URL=https://api.deepseek.com\n"
    )


def test_complete_file_wins_over_stale_layers(tmp_path):
    configuration(tmp_path / ".env", model="hand-edited")
    configuration(tmp_path / ".env.common", model="stale")
    assert read_configuration(tmp_path)["MEDAGENT_LLM_MODEL"] == "hand-edited"


def test_layers_work_without_generated_file(tmp_path):
    configuration(tmp_path / ".env.common")
    (tmp_path / ".env.dsh").write_text("MEDAGENT_BACKEND=dsh\n")
    settings = read_configuration(tmp_path)
    assert settings["MEDAGENT_BACKEND"] == "dsh"
    assert settings["MEDAGENT_LLM_BASE_URL"].endswith("/anthropic")
    assert read_configuration(tmp_path, backend="compatible")["MEDAGENT_BACKEND"] == "compatible"


def test_explicit_file_never_falls_back(tmp_path):
    configuration(tmp_path / ".env")
    with pytest.raises(FileNotFoundError):
        read_configuration(tmp_path, env_file=tmp_path / "missing")


def test_partial_parser_preserves_quoted_key_and_rejects_control_characters(tmp_path):
    path = tmp_path / ".env"
    path.write_text("export MEDAGENT_LLM_API_KEY='$(literal) # secret'\n")
    assert read_partial(path)["MEDAGENT_LLM_API_KEY"] == "$(literal) # secret"
    path.write_text("MEDAGENT_LLM_API_KEY='embedded\tcontrol'\n")
    with pytest.raises(ValueError, match="control"):
        read_partial(path)
