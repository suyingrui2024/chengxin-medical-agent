"""Own subprocess trees on POSIX and Windows; no taskkill-by-name or host scans."""
import os
import signal
import subprocess


def popen(argv, **kwargs):
    if os.name != "nt":
        return subprocess.Popen(argv, start_new_session=True, **kwargs)
    from .windows_process import launch

    return launch(argv, **kwargs)


def terminate(process):
    if os.name == "nt":
        from .windows_process import terminate_job

        terminate_job(process)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def release(process):
    if os.name == "nt":
        from .windows_process import close_job

        close_job(process)
