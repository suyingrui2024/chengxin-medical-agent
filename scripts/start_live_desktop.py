"""Explicitly launch the desktop with local .env settings; never print secrets."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit

from medagent.env_config import for_backend, read_env


def require_dsh_node():
    node = shutil.which("node")
    if not node:
        raise SystemExit(
            "DSH live desktop requires Node 22.19+; install it and retry "
            "(macOS: brew install node@22)"
        )
    try:
        output = subprocess.run(
            [node, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise SystemExit("Unable to verify the Node runtime required by DSH") from None
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", output)
    if not match or tuple(map(int, match.groups())) < (22, 19, 0):
        raise SystemExit(
            f"DSH live desktop requires Node 22.19+; found {output or 'unknown'}"
        )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicit local configuration file for the native desktop application",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check settings without starting or calling a model",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = read_env(args.env_file or root / ".env")
    mode = settings.get("MEDAGENT_BACKEND", "compatible")
    if mode not in {"compatible", "dsh"}:
        raise SystemExit("Live desktop requires MEDAGENT_BACKEND=compatible or dsh")
    settings = for_backend(settings, mode)
    node_version = require_dsh_node() if mode == "dsh" else None
    if args.check:
        print(
            json.dumps(
                {
                    "backend": mode,
                    "model": settings["MEDAGENT_LLM_MODEL"],
                    "endpoint_host": urlsplit(
                        settings["MEDAGENT_LLM_BASE_URL"]
                    ).hostname,
                    "key_configured": True,
                    **({"node": node_version} if node_version else {}),
                },
                ensure_ascii=False,
            )
        )
        return
    env = {**os.environ, **settings, "MEDAGENT_DESKTOP_LIVE": "1"}
    env.pop("ELECTRON_RUN_AS_NODE", None)
    if os.name == "nt":
        metadata = json.loads((root / "package.json").read_text(encoding="utf-8"))
        candidates = [
            root
            / f"dist/desktop/Chengxin-{metadata['version']}-win32-x64/Chengxin.exe",
            root / "node_modules/electron/dist/electron.exe",
        ]
        for index, binary in enumerate(candidates):
            if binary.is_file():
                os.execve(
                    str(binary), [str(binary), *([str(root)] if index else [])], env
                )
        raise OSError(
            "Windows Electron runtime is missing; run scripts/windows.ps1 -Mode Setup"
        )
    os.execvpe("bash", ["bash", str(root / "scripts/start-desktop.sh")], env)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(
            f"Live desktop configuration failed ({type(exc).__name__})"
        ) from None
