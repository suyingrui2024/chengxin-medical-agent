"""Platform filesystem operations used by trusted workspace bookkeeping.

POSIX keeps its native fd-relative implementation. Windows opens children relative
to a pinned directory handle and rejects reparse points and alternate data streams.
"""
import os as _os

if _os.name != "nt":
    os = _os
    import fcntl
else:
    import ctypes as C
    from ctypes import wintypes as W
    import errno
    import msvcrt
    import stat as _stat
    from types import SimpleNamespace

    kernel = C.WinDLL("kernel32", use_last_error=True)
    ntdll = C.WinDLL("ntdll")
    HANDLE = W.HANDLE
    INVALID = C.c_void_p(-1).value

    class UNICODE_STRING(C.Structure):
        _fields_ = [
            ("Length", W.USHORT),
            ("MaximumLength", W.USHORT),
            ("Buffer", W.LPWSTR),
        ]

    class OBJECT_ATTRIBUTES(C.Structure):
        _fields_ = [
            ("Length", W.ULONG),
            ("RootDirectory", HANDLE),
            ("ObjectName", C.POINTER(UNICODE_STRING)),
            ("Attributes", W.ULONG),
            ("SecurityDescriptor", W.LPVOID),
            ("SecurityQualityOfService", W.LPVOID),
        ]

    class IO_STATUS_BLOCK(C.Structure):
        _fields_ = [("Status", C.c_void_p), ("Information", C.c_size_t)]

    class FILE_INFO(C.Structure):
        _fields_ = [
            ("attributes", W.DWORD),
            ("created", W.FILETIME),
            ("accessed", W.FILETIME),
            ("written", W.FILETIME),
            ("volume", W.DWORD),
            ("size_high", W.DWORD),
            ("size_low", W.DWORD),
            ("links", W.DWORD),
            ("index_high", W.DWORD),
            ("index_low", W.DWORD),
        ]

    kernel.CreateFileW.argtypes = [
        W.LPCWSTR,
        W.DWORD,
        W.DWORD,
        W.LPVOID,
        W.DWORD,
        W.DWORD,
        HANDLE,
    ]
    kernel.CreateFileW.restype = HANDLE
    kernel.GetFileInformationByHandle.argtypes = [HANDLE, C.POINTER(FILE_INFO)]
    kernel.GetFileInformationByHandle.restype = W.BOOL
    kernel.GetFinalPathNameByHandleW.argtypes = [HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = W.DWORD
    kernel.CloseHandle.argtypes = [HANDLE]
    ntdll.NtCreateFile.argtypes = [
        C.POINTER(HANDLE),
        W.ULONG,
        C.POINTER(OBJECT_ATTRIBUTES),
        C.POINTER(IO_STATUS_BLOCK),
        W.LPVOID,
        W.ULONG,
        W.ULONG,
        W.ULONG,
        W.ULONG,
        W.LPVOID,
        W.ULONG,
    ]
    ntdll.NtCreateFile.restype = W.LONG
    ntdll.RtlNtStatusToDosError.argtypes = [W.LONG]
    ntdll.RtlNtStatusToDosError.restype = W.ULONG

    def checked_name(value):
        name = _os.fsdecode(value)
        if (
            not name
            or name in {".", ".."}
            or name[-1] in " ."
            or any(ord(ch) < 32 or ch in '\\/:<>"|?*' for ch in name)
            or name.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }
        ):
            raise OSError(errno.EINVAL, "Invalid Windows relative filename")
        return name

    def fd_path(fd):
        buffer = C.create_unicode_buffer(32768)
        size = kernel.GetFinalPathNameByHandleW(
            msvcrt.get_osfhandle(fd), buffer, len(buffer), 0
        )
        if not size or size >= len(buffer):
            raise C.WinError(C.get_last_error())
        return buffer.value

    def child_path(name, fd):
        return _os.path.join(fd_path(fd), checked_name(name))

    class Entry:
        def __init__(self, value):
            self.value = value
            self.name, self.path = value.name, value.path

        def stat(self, *, follow_symlinks=True):
            info = self.value.stat(follow_symlinks=follow_symlinks)
            if not follow_symlinks and info.st_file_attributes & 0x400:
                values = {
                    name: getattr(info, name)
                    for name in dir(info)
                    if name.startswith("st_")
                }
                values["st_mode"] = _stat.S_IFLNK | (info.st_mode & 0o777)
                return SimpleNamespace(**values)
            return info

        def is_symlink(self):
            return bool(
                self.value.stat(follow_symlinks=False).st_file_attributes & 0x400
            )

        def is_dir(self, *, follow_symlinks=True):
            return _stat.S_ISDIR(self.stat(follow_symlinks=follow_symlinks).st_mode)

        def is_file(self, *, follow_symlinks=True):
            return _stat.S_ISREG(self.stat(follow_symlinks=follow_symlinks).st_mode)

    class Scan:
        def __init__(self, value):
            self.value = value

        def __iter__(self):
            return self

        def __next__(self):
            return Entry(next(self.value))

        def close(self):
            self.value.close()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    class WindowsOS:
        O_DIRECTORY = 0x40000000
        O_NOFOLLOW = 0x20000000
        O_NONBLOCK = 0

        def __getattr__(self, name):
            return getattr(_os, name)

        def open(self, path, flags, mode=0o777, *, dir_fd=None):
            directory = bool(flags & self.O_DIRECTORY)
            nofollow = bool(flags & self.O_NOFOLLOW)
            access = 0x100000 | 0x80  # SYNCHRONIZE | FILE_READ_ATTRIBUTES
            access |= 1 if directory or not (flags & _os.O_WRONLY) else 0
            if flags & (_os.O_WRONLY | _os.O_RDWR):
                access |= 2 | 4 | 0x100
            share = (
                3 if directory else 7
            )  # Pin directory identity; regular files may be replaced.
            disposition = 1  # FILE_OPEN
            if flags & _os.O_CREAT:
                disposition = 2 if flags & _os.O_EXCL else 3  # CREATE / OPEN_IF
            if flags & _os.O_TRUNC:
                disposition = 5  # OVERWRITE_IF
            if dir_fd is not None:
                name = checked_name(path)
                text = C.create_unicode_buffer(
                    name, len(name.encode("utf-16-le")) // 2 + 1
                )
                unicode = UNICODE_STRING(
                    len(name.encode("utf-16-le")),
                    len(name.encode("utf-16-le")) + 2,
                    C.cast(text, W.LPWSTR),
                )
                attributes = OBJECT_ATTRIBUTES(
                    C.sizeof(OBJECT_ATTRIBUTES),
                    msvcrt.get_osfhandle(dir_fd),
                    C.pointer(unicode),
                    0x40,
                    None,
                    None,
                )
                handle = HANDLE()
                status = IO_STATUS_BLOCK()
                code = ntdll.NtCreateFile(
                    C.byref(handle),
                    access,
                    C.byref(attributes),
                    C.byref(status),
                    None,
                    0x80,
                    share,
                    disposition,
                    0x20 | 0x200000 | (1 if directory else 0x40),
                    None,
                    0,
                )
                if code < 0:
                    raise C.WinError(ntdll.RtlNtStatusToDosError(code))
                handle = handle.value
            else:
                creation = {1: 3, 2: 1, 3: 4, 5: 2}[disposition]
                handle = kernel.CreateFileW(
                    _os.path.abspath(path),
                    access,
                    share,
                    None,
                    creation,
                    0x02000000 | 0x00200000,
                    None,
                )
                if handle == INVALID:
                    raise C.WinError(C.get_last_error())
            try:
                info = FILE_INFO()
                if not kernel.GetFileInformationByHandle(handle, C.byref(info)):
                    raise C.WinError(C.get_last_error())
                if nofollow and info.attributes & 0x400:
                    raise OSError(errno.ELOOP, "Reparse points are not workspace files")
                if directory != bool(info.attributes & 0x10):
                    raise OSError(
                        errno.ENOTDIR if directory else errno.EISDIR,
                        "Unexpected filesystem object",
                    )
                crt_flags = _os.O_BINARY | (
                    flags & (_os.O_RDONLY | _os.O_WRONLY | _os.O_RDWR | _os.O_APPEND)
                )
                return msvcrt.open_osfhandle(handle, crt_flags)
            except BaseException:
                kernel.CloseHandle(handle)
                raise

        def scandir(self, path):
            return Scan(_os.scandir(fd_path(path) if isinstance(path, int) else path))

        def mkdir(self, path, mode=0o777, *, dir_fd=None):
            return _os.mkdir(
                child_path(path, dir_fd) if dir_fd is not None else path, mode
            )

        def unlink(self, path, *, dir_fd=None):
            return _os.unlink(child_path(path, dir_fd) if dir_fd is not None else path)

        def replace(self, source, target, *, src_dir_fd=None, dst_dir_fd=None):
            return _os.replace(
                child_path(source, src_dir_fd) if src_dir_fd is not None else source,
                child_path(target, dst_dir_fd) if dst_dir_fd is not None else target,
            )

        def stat(self, path, *, dir_fd=None, follow_symlinks=True):
            return _os.stat(
                child_path(path, dir_fd) if dir_fd is not None else path,
                follow_symlinks=follow_symlinks,
            )

        def readlink(self, path, *, dir_fd=None):
            return _os.readlink(
                child_path(path, dir_fd) if dir_fd is not None else path
            )

        def fsync(self, fd):
            if not _stat.S_ISDIR(_os.fstat(fd).st_mode):
                _os.fsync(fd)

    os = WindowsOS()

    class WindowsLocks:
        LOCK_EX, LOCK_NB = 2, 4

        def flock(self, fd, flags):
            if _stat.S_ISDIR(_os.fstat(fd).st_mode):
                # Directory handles already prevent replacement; use a named semaphore
                # to serialize independent project selections by volume/file ID.
                info = _os.fstat(fd)
                kernel.CreateSemaphoreW.argtypes = [W.LPVOID, W.LONG, W.LONG, W.LPCWSTR]
                kernel.CreateSemaphoreW.restype = HANDLE
                kernel.WaitForSingleObject.argtypes = [HANDLE, W.DWORD]
                handle = kernel.CreateSemaphoreW(
                    None, 1, 1, f"Local\\Chengxin-project-{info.st_dev}-{info.st_ino}"
                )
                if not handle:
                    raise C.WinError(C.get_last_error())
                if kernel.WaitForSingleObject(handle, 0) not in (0, 0x80):
                    kernel.CloseHandle(handle)
                    raise BlockingIOError(errno.EAGAIN, "Project occupied")
                _mutexes[fd] = handle
            else:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise BlockingIOError(errno.EAGAIN, "Workspace occupied") from None

    _mutexes = {}
    _close = os.close

    def close(fd):
        handle = _mutexes.pop(fd, None)
        if handle:
            kernel.ReleaseSemaphore.argtypes = [HANDLE, W.LONG, W.LPVOID]
            kernel.ReleaseSemaphore(handle, 1, None)
            kernel.CloseHandle(handle)
        _close(fd)

    os.close = close
    fcntl = WindowsLocks()
