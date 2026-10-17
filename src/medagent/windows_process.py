"""Native Windows job ownership. Processes start suspended until assigned.

Job objects provide lifecycle control; they are not filesystem security sandboxes.
"""
import ctypes as C
from ctypes import wintypes as W
import subprocess

kernel = C.WinDLL("kernel32", use_last_error=True)


class LIMITS(C.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", C.c_int64),
        ("PerJobUserTimeLimit", C.c_int64),
        ("LimitFlags", W.DWORD),
        ("MinimumWorkingSetSize", C.c_size_t),
        ("MaximumWorkingSetSize", C.c_size_t),
        ("ActiveProcessLimit", W.DWORD),
        ("Affinity", C.c_size_t),
        ("PriorityClass", W.DWORD),
        ("SchedulingClass", W.DWORD),
    ]


class IO_COUNTERS(C.Structure):
    _fields_ = [
        (name, C.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class EXTENDED_LIMITS(C.Structure):
    _fields_ = [
        ("BasicLimitInformation", LIMITS),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", C.c_size_t),
        ("JobMemoryLimit", C.c_size_t),
        ("PeakProcessMemoryUsed", C.c_size_t),
        ("PeakJobMemoryUsed", C.c_size_t),
    ]


class THREAD_ENTRY(C.Structure):
    _fields_ = [
        ("dwSize", W.DWORD),
        ("cntUsage", W.DWORD),
        ("th32ThreadID", W.DWORD),
        ("th32OwnerProcessID", W.DWORD),
        ("tpBasePri", W.LONG),
        ("tpDeltaPri", W.LONG),
        ("dwFlags", W.DWORD),
    ]


kernel.CreateJobObjectW.argtypes = [W.LPVOID, W.LPCWSTR]
kernel.CreateJobObjectW.restype = W.HANDLE
kernel.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD]
kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
kernel.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
kernel.CloseHandle.argtypes = [W.HANDLE]
kernel.GetCurrentProcess.restype = W.HANDLE
kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
kernel.Thread32First.argtypes = [W.HANDLE, C.POINTER(THREAD_ENTRY)]
kernel.Thread32Next.argtypes = [W.HANDLE, C.POINTER(THREAD_ENTRY)]
kernel.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel.OpenThread.restype = W.HANDLE
kernel.ResumeThread.argtypes = [W.HANDLE]
kernel.ResumeThread.restype = W.DWORD


def create_job():
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise C.WinError(C.get_last_error())
    limits = EXTENDED_LIMITS()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, C.byref(limits), C.sizeof(limits)):
        kernel.CloseHandle(handle)
        raise C.WinError(C.get_last_error())
    return handle


def resume(pid):
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        entry = THREAD_ENTRY()
        entry.dwSize = C.sizeof(entry)
        more = kernel.Thread32First(snapshot, C.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = kernel.OpenThread(2, False, entry.th32ThreadID)
                if not thread:
                    raise C.WinError(C.get_last_error())
                try:
                    if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                        raise C.WinError(C.get_last_error())
                    return
                finally:
                    kernel.CloseHandle(thread)
            more = kernel.Thread32Next(snapshot, C.byref(entry))
        raise OSError("Suspended process has no resumable thread")
    finally:
        kernel.CloseHandle(snapshot)


def launch(argv, **kwargs):
    job = create_job()
    child = None
    try:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 4 | 0x08000000
        child = subprocess.Popen(argv, **kwargs)
        if not kernel.AssignProcessToJobObject(job, int(child._handle)):
            raise C.WinError(C.get_last_error())
        child._medagent_job = job
        resume(child.pid)
        return child
    except BaseException:
        if child is not None:
            child.kill()
            child.wait(timeout=5)
        kernel.CloseHandle(job)
        raise


def terminate_job(child):
    handle = getattr(child, "_medagent_job", None)
    if handle:
        kernel.TerminateJobObject(handle, 1)
    elif child.poll() is None:
        child.kill()


def close_job(child):
    handle = getattr(child, "_medagent_job", None)
    if handle:
        child._medagent_job = None
        kernel.CloseHandle(handle)


_own_job = None


def own_process_tree():
    """Assign the sidecar before it can spawn SDKs; parent crashes reap descendants."""
    global _own_job
    if _own_job:
        return
    job = create_job()
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        kernel.CloseHandle(job)
        raise C.WinError(C.get_last_error())
    _own_job = job
