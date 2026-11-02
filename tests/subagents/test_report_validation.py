"""Report validation's rejection paths, end to end.

The validation loop is the subagent's contract with the rest of the case: what
it lets through is what the consultation will later read.  These tests drive
``run_spawn`` with a scripted backend and assert on the branch result, the same
way the production caller sees it.
"""
import unittest

from medagent.core.events import EventStore

from tests.subagents.test_subagents import (
    StubBackend,
    coordinator,
    make_state,
    report,
    spawn_proposal,
)


class ReportValidationTests(unittest.TestCase):
    def spawn(self, backend, kind="evidence_lookup"):
        state = make_state()
        result = coordinator(backend, EventStore()).run_spawn(
            state, spawn_proposal(kind=kind), session_id=state.session_id
        )
        return result["branches"][0]

    def test_a_report_that_is_not_an_object_fails_the_branch(self):
        """A malformed reply never reaches validation; the branch times out.

        The child loop treats an unparseable reply as no reply, so
        ``run_spawn`` cannot reach the ``report_not_object`` guard — that
        branch is entry-point defence for direct callers and stays untested
        from the outside on purpose.
        """
        branch = self.spawn(StubBackend(lambda sid, messages, cancelled: ["nope"]))
        self.assertEqual(branch["status"], "budget_exhausted")

    def test_an_overlong_conclusion_is_truncated_not_dropped(self):
        branch = self.spawn(
            StubBackend(
                lambda sid, messages, cancelled: report(
                    conclusion="这是一段很长的结论" * 200
                )
            )
        )
        conclusion = branch["conclusion"]
        self.assertTrue(conclusion)
        self.assertLessEqual(
            len(conclusion),
            1200,
            "truncation, not rejection, is the contract for length",
        )
        self.assertIn("conclusion_truncated", branch["validation"]["notes"])

    def test_a_conclusion_with_disposition_phrasing_is_dropped_wholesale(self):
        branch = self.spawn(
            StubBackend(
                lambda sid, messages, cancelled: report(
                    conclusion="综合以上，可以确诊为上呼吸道感染"
                )
            )
        )
        self.assertEqual(branch["conclusion"], "")
        # One claim out of one is a 100% drop rate: the fuse rejects.
        self.assertEqual(branch["status"], "rejected_by_validation")
        self.assertIn("conclusion_dropped_lint", branch["validation"]["notes"])
        self.assertEqual(branch["validation"]["dropped_claims"], 1)

    def test_evidence_lookup_without_one_verified_quote_is_rejected(self):
        branch = self.spawn(StubBackend(lambda sid, messages, cancelled: report()))
        self.assertEqual(branch["status"], "rejected_by_validation")
        self.assertEqual(branch["findings"], [])
