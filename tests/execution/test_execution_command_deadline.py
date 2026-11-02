"""Real SDK processes retain their launch deadline across another user turn."""

import os
import unittest

from tests.support.execution_case import exercise_command_deadline




@unittest.skipUnless(os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real isolated SDK")
class CommandDeadlineTests(unittest.TestCase):
    def test_original_command_deadline_survives_an_independent_followup(self):
        exercise_command_deadline(self)
