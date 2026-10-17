"""Run a setup command solely in the disposable Debian filesystem."""
from pathlib import Path
import os
import subprocess
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
base = ["/usr/bin/bwrap", "--unshare-all", "--share-net", "--uid", "0", "--gid", "0", "--die-with-parent", "--clearenv", "--bind", str(HERE / "rootfs"), "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf", "--ro-bind", "/etc/ssl/certs/ca-certificates.crt", "/bootstrap-ca.crt"]
env = {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/root", "LANG": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive"}
for name in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]:
    value = os.environ.get(name)
    if value and (name.lower() == "no_proxy" or not urlsplit(value).username):
        env[name] = value
for key, value in env.items():
    base += ["--setenv", key, value]
raise SystemExit(subprocess.run(base + ["--"] + sys.argv[1:]).returncode)
