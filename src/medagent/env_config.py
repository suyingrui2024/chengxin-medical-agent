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
    "MEDAGENT_LLM_REASONING_EFFORT",
    "MEDAGENT_VISION_MODEL",
    "MEDAGENT_DSH_REVIEW_MODE",
    "MEDAGENT_WEB_TOOLS",
    "MEDAGENT_SUBAGENT_MODE",
    # 注意：故意没有 MEDAGENT_SUBAGENT_MODEL。子智能体经
    # SubagentCoordinator 调用的是父引擎同一个已构造的 backend
    # （decide_cancellable 无逐调用模型参数，模型在后端构造时由
    # MEDAGENT_LLM_MODEL 定死），没有可单独覆盖的接缝；曾收录该
    # 变量只会造成「设了却不生效」的假象，故移除。子智能体模型
    # 随父后端，如需更换请改 MEDAGENT_LLM_MODEL。
    "MEDAGENT_SUBAGENT_WEB",
    "MEDAGENT_SUBAGENT_DAILY_TOKEN_CAP",
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
    review_mode = settings.get("MEDAGENT_DSH_REVIEW_MODE", "shadow")
    if review_mode not in {"shadow", "advisory"}:
        raise ValueError("MEDAGENT_DSH_REVIEW_MODE must be shadow or advisory")
    settings["MEDAGENT_DSH_REVIEW_MODE"] = review_mode
    web_tools = settings.get("MEDAGENT_WEB_TOOLS", "off")
    if web_tools not in {"off", "education", "review", "all"}:
        raise ValueError("MEDAGENT_WEB_TOOLS must be off, education, review or all")
    settings["MEDAGENT_WEB_TOOLS"] = web_tools
    subagent_mode = settings.get("MEDAGENT_SUBAGENT_MODE", "off")
    if subagent_mode not in {"off", "shadow", "advisory"}:
        raise ValueError("MEDAGENT_SUBAGENT_MODE must be off, shadow or advisory")
    settings["MEDAGENT_SUBAGENT_MODE"] = subagent_mode
    subagent_web = settings.get("MEDAGENT_SUBAGENT_WEB", "off")
    if subagent_web not in {"on", "off"}:
        raise ValueError("MEDAGENT_SUBAGENT_WEB must be on or off")
    settings["MEDAGENT_SUBAGENT_WEB"] = subagent_web
    daily_cap = settings.get("MEDAGENT_SUBAGENT_DAILY_TOKEN_CAP")
    if daily_cap is not None:
        try:
            parsed_cap = int(daily_cap)
        except (TypeError, ValueError):
            parsed_cap = 0
        if parsed_cap <= 0:
            raise ValueError(
                "MEDAGENT_SUBAGENT_DAILY_TOKEN_CAP must be a positive integer"
            )
        settings["MEDAGENT_SUBAGENT_DAILY_TOKEN_CAP"] = str(parsed_cap)
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
