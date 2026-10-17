"""Install test OS dependencies inside the downloaded rootfs only."""

from pathlib import Path
import json
import os
import subprocess
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOTFS = HERE / "rootfs"
assert ROOTFS.is_dir() and (ROOTFS / "etc/debian_version").is_file()
sources = ROOTFS / "etc/apt/sources.list.d/debian.sources"
sources.write_text(sources.read_text().replace("http://deb.debian.org", "https://deb.debian.org"))
policy = ROOTFS / "usr/sbin/policy-rc.d"
policy.write_text("#!/bin/sh\nexit 101\n")
policy.chmod(0o755)
base = [
    "/usr/bin/bwrap", "--unshare-all", "--share-net", "--uid", "0", "--gid", "0",
    "--die-with-parent", "--clearenv", "--bind", str(ROOTFS), "/",
    "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
    "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
    "--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs",
]
environment = {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/root", "LANG": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive"}
for name in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]:
    value = os.environ.get(name)
    if value and (name.lower() == "no_proxy" or not urlsplit(value).username):
        environment[name] = value
for name, value in environment.items():
    base += ["--setenv", name, value]
common = ["/usr/bin/apt-get", "-o", "APT::Sandbox::User=root", "-o", "Dpkg::Use-Pty=0"]
if len(sys.argv) > 1 and sys.argv[1] == "fonts":
    commands = [("install-chinese-fonts.log", common + ["install", "-y", "--no-install-recommends", "fonts-noto-cjk"])]
else:
    packages = ["python3", "libgtk-3-0", "libnss3", "libnspr4", "libasound2", "libgbm1", "libxss1", "libxtst6", "libatk-bridge2.0-0", "libcups2", "libxrandr2", "libxdamage1", "libxcomposite1", "libxfixes3", "libxkbcommon0", "libxshmfence1", "libx11-xcb1", "fontconfig", "fonts-dejavu-core", "ca-certificates"]
    (HERE / "requested-packages.json").write_text(json.dumps(packages, indent=2) + "\n")
    commands = [("apt-update.log", common + ["update"]), ("install-runtime.log", common + ["install", "-y", "--no-install-recommends"] + packages)]
for name, command in commands:
    with (HERE / name).open("xb") as output:
        result = subprocess.run(base + ["--"] + command, stdout=output, stderr=subprocess.STDOUT, timeout=600)
    print(json.dumps({"stage": name, "exit_code": result.returncode}), flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
result = subprocess.run(base + ["--", "/usr/bin/dpkg-query", "-W", "-f", "${Package}\t${Version}\n"], capture_output=True, check=True)
name = "installed-with-cjk.tsv" if len(sys.argv) > 1 else "installed-runtime.tsv"
(HERE / name).write_bytes(result.stdout)
