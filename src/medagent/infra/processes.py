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


def terminate_gracefully(process):
    """Ask the process tree to stop before ``terminate`` kills it.

    POSIX sends SIGTERM to the group so the worker can flush; Windows has no
    equivalent signal for a job object, so it goes straight to ``terminate``.
    """
    if os.name == "nt":
        terminate(process)
        return
    os.killpg(process.pid, signal.SIGTERM)


def own_process_tree():
    """Take ownership of our own process tree, where the OS needs it.

    On Windows a child outlives its parent unless it is assigned to a job
    object first, so the desktop sidecar claims the tree at startup. POSIX
    gets this from ``start_new_session`` in ``popen`` and has nothing to do.
    """
    if os.name == "nt":
        from .windows_process import own_process_tree as _own

        _own()


def release(process):
    if os.name == "nt":
        from .windows_process import close_job

        close_job(process)
