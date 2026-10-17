"""Check the packaged desktop with an empty HOME and no development checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    package = args.package.resolve(strict=True)
    output = args.output_directory.resolve()
    if output.exists():
        parser.error("A fresh output directory is required")
    binary = package / "Chengxin"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("The package must contain an executable Chengxin")
    xauthority = Path(os.environ.get("XAUTHORITY", ""))
    if not os.environ.get("DISPLAY") or not xauthority.is_file():
        parser.error("DISPLAY and a readable XAUTHORITY are required")
    bwrap = shutil.which("bwrap")
    node = shutil.which("node")
    if not bwrap or not node:
        parser.error("bubblewrap and Node are required")
    node_path = Path(
        subprocess.check_output([node, "-p", "process.execPath"], text=True).strip()
    ).resolve()
    output.mkdir(parents=True)
    command = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
    ]
    for name in ("/usr", "/lib", "/lib64"):
        if Path(name).exists():
            command.extend(["--ro-bind", name, name])
    command.extend(
        [
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/sbin",
            "/sbin",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/home",
            "--dir",
            "/home/clean",
            "--dir",
            "/etc",
            "--tmpfs",
            "/run",
            "--dir",
            "/run/user/1000",
        ]
    )
    for name in (
        "/etc/fonts",
        "/etc/ld.so.cache",
        "/etc/alternatives",
        "/etc/localtime",
    ):
        if Path(name).exists():
            command.extend(["--ro-bind", str(Path(name).resolve()), name])
    mounts = {
        package: "/app",
        node_path.parent.parent: "/opt/node",
        Path("/tmp/.X11-unix"): "/tmp/.X11-unix",
        xauthority.resolve(): "/test-xauthority",
        root / "scripts/clean-desktop-probe.mjs": "/probe.mjs",
    }
    for name in ("@playwright/test", "playwright", "playwright-core"):
        mounts[root / "node_modules" / name] = "/test-tools/node_modules/" + name
    for source, target in mounts.items():
        command.extend(["--ro-bind", str(source), target])
    command.extend(["--bind", str(output), "/out"])
    environment = {
        "HOME": "/home/clean",
        "PATH": "/opt/node/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "DISPLAY": os.environ["DISPLAY"],
        "XAUTHORITY": "/test-xauthority",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "PYTHONDONTWRITEBYTECODE": "1",
        "MEDAGENT_PYTHON": "/usr/bin/python3",
    }
    for name, value in environment.items():
        command.extend(["--setenv", name, value])
    command.extend(["--chdir", "/home/clean", "--", "/opt/node/bin/node", "/probe.mjs"])
    identity = {
        "package_version": json.loads(
            (package / "resources/app/package.json").read_text()
        )["version"],
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(
            (root / "scripts/clean-desktop-probe.mjs").read_bytes()
        ).hexdigest(),
        "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "display_shared_with_host": True,
        "host_system_libraries_readonly": True,
        "external_network_namespace_shared": False,
        "development_checkout_mounted": False,
        "model_requests": 0,
        "scope": "Same-host isolated empty-user-environment check, not a new-machine or independent operating-system installation.",
    }
    (output / "identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    try:
        result = subprocess.run(command, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as error:
        (output / "stdout.log").write_bytes(error.stdout or b"")
        (output / "stderr.log").write_bytes(error.stderr or b"")
        (output / "launcher-result.json").write_text(
            json.dumps({"passed": False, "error": "timeout"}) + "\n"
        )
        raise SystemExit("Isolated desktop timed out; evidence retained")
    (output / "stdout.log").write_bytes(result.stdout)
    (output / "stderr.log").write_bytes(result.stderr)
    probe_path = output / "probe.json"
    probe = json.loads(probe_path.read_text()) if probe_path.is_file() else {}
    passed = result.returncode == 0 and probe.get("passed") is True
    summary = {
        "passed": passed,
        "exit_code": result.returncode,
        "probe_report_present": bool(probe),
    }
    (output / "launcher-result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
