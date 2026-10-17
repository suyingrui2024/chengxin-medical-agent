import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli.py")


class CliTests(unittest.TestCase):
    def run_cli(self, text):
        proc = subprocess.run(
            [sys.executable, CLI],
            input=text,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_cli_reads_csv_from_stdin(self):
        out = self.run_cli("group,value\nc,3\nc,5\n")
        self.assertEqual(
            out,
            {"groups": [{"group": "c", "count": 2, "min": "3", "max": "5", "mean": "4.00"}]},
        )

    def test_cli_empty_input(self):
        # A header-only CSV has no data rows.
        self.assertEqual(self.run_cli("group,value\n"), {"groups": []})


if __name__ == "__main__":
    unittest.main()
