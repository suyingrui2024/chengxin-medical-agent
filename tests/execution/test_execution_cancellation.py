"""Independent task expiry and explicit workspace stop through the real SDK."""
import os
import unittest

from tests.support.execution_case import (
    exercise_root_job_cancellation,
    exercise_task_cancellation,
)










@unittest.skipUnless(os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real isolated SDK")
class TaskCancellationRuntimeTests(unittest.TestCase):
    def test_independent_grant_expiry_and_explicit_workspace_stop(self):
        exercise_task_cancellation(self)

    def test_background_root_jobs_retain_their_grants(self):
        exercise_root_job_cancellation(self)
