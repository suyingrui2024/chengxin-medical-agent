import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CliTests(unittest.TestCase):
    def run_cli(self, csv_text):
        proc = subprocess.run(
            [sys.executable, "cli.py"],
            input=csv_text,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_cli_outputs_groups(self):
        csv_text = "group,value\nb,3\na,1\na,2\n"
        self.assertEqual(
            self.run_cli(csv_text),
            {
                "groups": [
                    {"group": "a", "count": 2, "min": "1", "max": "2", "mean": "1.50"},
                    {"group": "b", "count": 1, "min": "3", "max": "3", "mean": "3.00"},
                ]
            },
        )

    def test_cli_empty_input(self):
        self.assertEqual(self.run_cli(""), {"groups": []})

    def test_cli_header_only(self):
        self.assertEqual(self.run_cli("group,value\n"), {"groups": []})


if __name__ == "__main__":
    unittest.main()
