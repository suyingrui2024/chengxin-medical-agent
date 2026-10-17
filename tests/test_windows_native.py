"""Real Win32 filesystem and process tests. Never pretend Linux is Windows."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from medagent.execution import ExecutionManager, safe_file
from medagent.platform_fs import os as workspace_os
from medagent.processes import popen, terminate, release


@unittest.skipUnless(os.name == "nt", "requires the real Windows kernel")
class NativeWindowsTests(unittest.TestCase):
    def test_handle_relative_unicode_files_reparse_points_and_preview(self):
        with tempfile.TemporaryDirectory(prefix="chengxin ") as raw:
            root = Path(raw) / "中文 project's workspace"
            root.mkdir()
            fd = workspace_os.open(root, workspace_os.O_RDONLY | workspace_os.O_DIRECTORY | workspace_os.O_NOFOLLOW)
            try:
                workspace_os.mkdir("资料", dir_fd=fd)
                directory = workspace_os.open("资料", workspace_os.O_RDONLY | workspace_os.O_DIRECTORY | workspace_os.O_NOFOLLOW, dir_fd=fd)
                try:
                    file = workspace_os.open("结果.txt", workspace_os.O_CREAT | workspace_os.O_EXCL | workspace_os.O_WRONLY | workspace_os.O_NOFOLLOW, 0o600, dir_fd=directory)
                    workspace_os.write(file, "原生 Windows".encode())
                    workspace_os.close(file)
                finally:
                    workspace_os.close(directory)
                self.assertEqual(safe_file(root, "资料/结果.txt")["text"], "原生 Windows")
                self.assertEqual([entry.name for entry in workspace_os.scandir(fd)], ["资料"])
                outside = Path(raw) / "outside"
                outside.mkdir()
                (outside / "secret.txt").write_text("host-only")
                junction = root / "junction"
                subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)
                try:
                    with self.assertRaises((ValueError, OSError)):
                        safe_file(root, "junction/secret.txt")
                    with self.assertRaises(OSError):
                        workspace_os.open("资料:stream", workspace_os.O_RDONLY | workspace_os.O_NOFOLLOW, dir_fd=fd)
                finally:
                    junction.rmdir()
            finally:
                workspace_os.close(fd)

    def test_workspace_store_create_lock_restart_and_unicode_project(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = {"MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic", "MEDAGENT_LLM_MODEL": "fixture", "MEDAGENT_LLM_API_KEY": "fixture-only"}
            first = ExecutionManager(root=root / "storage")
            second = ExecutionManager(root=root / "storage")
            identity = None
            try:
                session = first.create(config)
                identity = session.id
                (session.directory / "中文.txt").write_text("content", encoding="utf-8")
                self.assertEqual(session.read_file("中文.txt")["text"], "content")
                self.assertEqual(first.catalog()[0]["id"], identity)
                with self.assertRaises(ValueError):
                    second.open(identity, config)
                first.release(identity)
                reopened = second.open(identity, config)
                self.assertEqual(reopened.read_file("中文.txt")["text"], "content")
            finally:
                first.close()
                second.close()

    def test_suspended_job_launch_owns_and_terminates_grandchild(self):
        with tempfile.TemporaryDirectory() as raw:
            marker = Path(raw) / "pids.json"
            code = "import json,subprocess,sys,time;from pathlib import Path;c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);Path(sys.argv[1]).write_text(json.dumps({'pid':c.pid}));time.sleep(120)"
            process = popen([sys.executable, "-I", "-c", code, str(marker)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 15
                while not marker.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(marker.is_file(), "suspended child did not resume")
                pid = json.loads(marker.read_text())["pid"]
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                from ctypes import wintypes as W
                kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
                kernel.OpenProcess.restype = W.HANDLE
                kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
                kernel.CloseHandle.argtypes = [W.HANDLE]
                child_handle = kernel.OpenProcess(0x100000, False, pid)
                self.assertTrue(child_handle)
                try:
                    terminate(process)
                    process.wait(timeout=10)
                    self.assertEqual(kernel.WaitForSingleObject(child_handle, 10000), 0, "grandchild survived the job")
                finally:
                    kernel.CloseHandle(child_handle)
            finally:
                terminate(process)
                release(process)
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
