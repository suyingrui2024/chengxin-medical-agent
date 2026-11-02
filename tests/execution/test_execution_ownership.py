"""Actual SDK descendants keep their original call grant and event identity."""

import os
import unittest

from tests.support.execution_case import exercise_child_ownership




@unittest.skipUnless(os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real isolated SDK")
class OwnershipRuntimeTests(unittest.TestCase):
    def test_original_child_grant_survives_a_foreground_followup(self):
        exercise_child_ownership(self)
