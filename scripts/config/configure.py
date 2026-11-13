#!/usr/bin/env python3
"""Create layered local model configuration for development."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from medagent.infra.env_config import (  # noqa: E402
    ALLOWED,
    for_backend,
    read_partial as read_env_partial,
)

COMMON_KEYS = (
    "MEDAGENT_BACKEND",
    "MEDAGENT_LLM_BASE_URL",
    "MEDAGENT_LLM_API_KEY",
    "MEDAGENT_LLM_MODEL",
    "MEDAGENT_VISION_MODEL",
    "MEDAGENT_LLM_MAX_TOKENS",
    "MEDAGENT_LLM_THINKING",
    "MEDAGENT_LLM_REASONING_EFFORT",
    "MEDAGENT_WEB_TOOLS",
)
DSH_KEYS = (
    "MEDAGENT_BACKEND",
    "MEDAGENT_LLM_BASE_URL",
    "MEDAGENT_DSH_REVIEW_MODE",
    "MEDAGENT_SUBAGENT_MODE",
    "MEDAGENT_SUBAGENT_WEB",
    "MEDAGENT_SUBAGENT_DAILY_TOKEN_CAP",
    "MEDAGENT_SUBAGENT_MODE_CASE_DIGEST",
    "MEDAGENT_SUBAGENT_MODE_EVIDENCE_LOOKUP",
    "MEDAGENT_SUBAGENT_MODE_SECOND_OPINION",
    "MEDAGENT_SUBAGENT_MODE_FOLLOWUP_BATCH",
)
KNOWN_KEYS = ALLOWED


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_partial(path: Path) -> dict[str, str]:
    return read_env_partial(path, allowed=KNOWN_KEYS)


def env_value(name: str, *sources: dict[str, str], default: str = "") -> str:
    for source in sources:
        value = source.get(name, "").strip()
        if value:
            return value
    value = os.environ.get(name, "").strip()
    return value or default


def prompt_text(label: str, current: str, *, interactive: bool) -> str:
    if not interactive:
        return current
    suffix = f" [{current}]" if current else ""
    entered = input(f"{label}{suffix}: ").strip()
    return entered or current


def prompt_secret(current: str, *, interactive: bool) -> str:
    if not interactive:
        return current
    hint = "（回车保留现有密钥）" if current else ""
    entered = getpass.getpass(f"DeepSeek API Key{hint}: ").strip()
    return entered or current


def validate_url(value: str, label: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label}必须是不含账号、查询参数或片段的 HTTP(S) 地址")
    if parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise ValueError(f"{label}连接外部服务时必须使用 HTTPS")


def direct_endpoint(value: str) -> str:
    return for_backend({"MEDAGENT_LLM_BASE_URL": value}, "compatible")["MEDAGENT_LLM_BASE_URL"]


def dsh_endpoint(value: str) -> str:
    return for_backend({"MEDAGENT_LLM_BASE_URL": value}, "dsh")["MEDAGENT_LLM_BASE_URL"]


def encode(value: str) -> str:
    return shlex.quote(value)


def atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", text=True
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
        os.replace(temporary, path)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_common(path: Path, values: dict[str, str]) -> None:
    lines = [
        "# Local development: common model-service layer.",
        "# Generated locally; this file is ignored by Git.",
    ]
    for name in COMMON_KEYS:
        if name in values and values[name] != "":
            lines.append(f"{name}={encode(values[name])}")
    atomic_write(path, lines)


def write_dsh(path: Path, values: dict[str, str]) -> None:
    lines = [
        "# Local development: DSH overlay layer.",
        "# Apply after .env.common; this file is ignored by Git.",
    ]
    for name in DSH_KEYS:
        if name in values:
            lines.append(f"{name}={encode(values[name])}")
    atomic_write(path, lines)


def write_full(path: Path, common: dict[str, str], dsh: dict[str, str]) -> None:
    merged = {**common, **dsh}
    lines = [
        (
            "# Local development: generated from .env.common + .env.dsh."
            if dsh
            else "# Local development: generated from .env.common."
        ),
        "# Regenerate with the matching script in scripts/config/<platform>/.",
    ]
    for name in dict.fromkeys((*COMMON_KEYS, *DSH_KEYS)):
        if merged.get(name, "") != "":
            lines.append(f"{name}={encode(merged[name])}")
    atomic_write(path, lines)


def build_common(root: Path, *, interactive: bool) -> dict[str, str]:
    layer = read_partial(root / ".env.common")
    existing = read_partial(root / ".env")
    base_url = direct_endpoint(
        env_value(
            "MEDAGENT_LLM_BASE_URL",
            layer,
            existing,
            default="https://api.deepseek.com",
        )
    )
    base_url = prompt_text("Chat Completions 服务地址", base_url, interactive=interactive)
    api_key = prompt_secret(
        env_value("MEDAGENT_LLM_API_KEY", layer, existing), interactive=interactive
    )
    model = prompt_text(
        "文字模型 ID",
        env_value("MEDAGENT_LLM_MODEL", layer, existing, default="deepseek-flash"),
        interactive=interactive,
    )
    vision_model = prompt_text(
        "图片模型 ID",
        env_value("MEDAGENT_VISION_MODEL", layer, existing, default="deepseek-flash"),
        interactive=interactive,
    )
    validate_url(base_url, "模型服务地址")
    if not api_key or api_key == "replace-me":
        raise ValueError("请填写真实的 DeepSeek API Key")
    if not model:
        raise ValueError("文字模型 ID 不能为空")
    values = {
        "MEDAGENT_BACKEND": "compatible",
        "MEDAGENT_LLM_BASE_URL": base_url,
        "MEDAGENT_LLM_API_KEY": api_key,
        "MEDAGENT_LLM_MODEL": model,
        "MEDAGENT_VISION_MODEL": vision_model,
    }
    for name in COMMON_KEYS[5:]:
        value = env_value(name, layer, existing)
        if value:
            values[name] = value
    return values


def build_dsh(root: Path, common: dict[str, str], *, interactive: bool) -> dict[str, str]:
    layer = read_partial(root / ".env.dsh")
    existing = read_partial(root / ".env")
    base_url = dsh_endpoint(
        env_value(
            "MEDAGENT_LLM_BASE_URL",
            layer,
            existing,
            common,
            default="https://api.deepseek.com/anthropic",
        )
    )
    base_url = prompt_text("DSH Messages 服务地址", base_url, interactive=interactive)
    review_mode = prompt_text(
        "后台复核模式（shadow/advisory）",
        env_value("MEDAGENT_DSH_REVIEW_MODE", layer, existing, default="shadow"),
        interactive=interactive,
    )
    validate_url(base_url, "DSH 服务地址")
    if review_mode not in {"shadow", "advisory"}:
        raise ValueError("后台复核模式只能是 shadow 或 advisory")
    return {
        **{
            name: env_value(name, layer, existing)
            for name in DSH_KEYS[3:]
            if env_value(name, layer, existing)
        },
        "MEDAGENT_BACKEND": "dsh",
        "MEDAGENT_LLM_BASE_URL": base_url,
        "MEDAGENT_DSH_REVIEW_MODE": review_mode,
    }


def configure_common(root: Path, *, interactive: bool) -> None:
    common = build_common(root, interactive=interactive)
    write_common(root / ".env.common", common)
    write_full(root / ".env", common, {})
    print("已生成通用层 .env.common，并将 .env 切换为直接模型开发配置。")
    print("启动：./scripts/app/start-desktop.sh --live")


def configure_dsh(root: Path, *, interactive: bool) -> None:
    common_path = root / ".env.common"
    if not common_path.exists():
        raise ValueError("尚未生成 .env.common；请先运行 01-configure-common.sh")
    common = read_partial(common_path)
    dsh = build_dsh(root, common, interactive=interactive)
    write_dsh(root / ".env.dsh", dsh)
    write_full(root / ".env", common, dsh)
    print("已生成 DSH 覆盖层 .env.dsh，并合并为完整 .env。")
    print("检查：npm run dsh:check")
    print("启动：./scripts/app/start-desktop.sh --live")


def configure_full(root: Path, *, interactive: bool) -> None:
    common = build_common(root, interactive=interactive)
    dsh = build_dsh(root, common, interactive=interactive)
    write_common(root / ".env.common", common)
    write_dsh(root / ".env.dsh", dsh)
    write_full(root / ".env", common, dsh)
    print("已生成 .env.common、.env.dsh 和可直接启动的完整 .env。")
    print("检查：npm run dsh:check")
    print("启动：./scripts/app/start-desktop.sh --live")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("common", "dsh", "full"))
    parser.add_argument(
        "--project-root",
        type=Path,
        default=project_root(),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("找不到项目根目录")
    action = {
        "common": configure_common,
        "dsh": configure_dsh,
        "full": configure_full,
    }[args.mode]
    try:
        action(root, interactive=not args.non_interactive)
    except (OSError, ValueError) as exc:
        print(f"配置失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
