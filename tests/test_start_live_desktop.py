import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.app.start_live_desktop import require_dsh_node


class LiveDesktopRuntimeTests(unittest.TestCase):
    @patch("scripts.app.start_live_desktop.shutil.which", return_value=None)
    def test_missing_node_has_an_actionable_error(self, _which):
        with self.assertRaisesRegex(SystemExit, "Node 22.19"):
            require_dsh_node()

    @patch("scripts.app.start_live_desktop.subprocess.run")
    @patch("scripts.app.start_live_desktop.shutil.which", return_value="/test/node")
    def test_old_node_is_rejected(self, _which, run):
        run.return_value = SimpleNamespace(stdout="v20.20.2\n")
        with self.assertRaisesRegex(SystemExit, "found v20.20.2"):
            require_dsh_node()

    @patch("scripts.app.start_live_desktop.subprocess.run")
    @patch("scripts.app.start_live_desktop.shutil.which", return_value="/test/node")
    def test_supported_node_is_reported(self, _which, run):
        run.return_value = SimpleNamespace(stdout="v22.23.3\n")
        self.assertEqual(require_dsh_node(), "v22.23.3")
        run.assert_called_once_with(
            ["/test/node", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )


if __name__ == "__main__":
    unittest.main()
