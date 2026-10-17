"""Explicit, non-executable .env loading for developer evaluation commands."""

from pathlib import Path
import shlex
from urllib.parse import urlsplit


ALLOWED = {
    "MEDAGENT_BACKEND",
    "MEDAGENT_LLM_BASE_URL",
    "MEDAGENT_LLM_API_KEY",
    "MEDAGENT_LLM_MODEL",
    "MEDAGENT_LLM_MAX_TOKENS",
    "MEDAGENT_LLM_THINKING",
}


def read_env(path):
    settings = {}
    for index, line in enumerate(
        Path(path).read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"Invalid .env syntax on line {index}") from None
        if parts and parts[0] == "export":
            parts.pop(0)
        assignment = " ".join(parts)
        if "=" not in assignment:
            raise ValueError(f"Invalid .env assignment on line {index}")
        name, value = assignment.split("=", 1)
        name = name.strip()
        if name in ALLOWED:
            value = value.strip()
            if any(ord(char) < 32 for char in value):
                raise ValueError("Configuration contains control characters")
            settings[name] = value
    for name in ("MEDAGENT_LLM_BASE_URL", "MEDAGENT_LLM_API_KEY", "MEDAGENT_LLM_MODEL"):
        if not settings.get(name) or settings[name] == "replace-me":
            raise ValueError(f"Missing configuration: {name}")
    url = urlsplit(settings["MEDAGENT_LLM_BASE_URL"])
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Evaluation endpoint must be HTTPS without embedded credentials or query parameters"
        )
    return settings


def for_backend(settings, backend):
    output = dict(settings)
    url = urlsplit(output["MEDAGENT_LLM_BASE_URL"])
    if url.hostname == "api.deepseek.com":
        if backend == "dsh" and url.path.rstrip("/") in {"", "/v1"}:
            output["MEDAGENT_LLM_BASE_URL"] = "https://api.deepseek.com/anthropic"
        elif backend == "compatible" and url.path.rstrip("/") == "/anthropic":
            output["MEDAGENT_LLM_BASE_URL"] = "https://api.deepseek.com"
    output["MEDAGENT_BACKEND"] = backend
    return output
