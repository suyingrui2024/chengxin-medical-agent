"""Regression tests for the 2026-10-23 six-way audit fixes (batch 1:
engine + conclusion chain). Each test names the audit finding it pins.
"""
import unittest

from medagent.domain import Assertion, CaseState, Fact, Phase, RiskAssessment, RiskLevel, Source
from medagent.runtime import build_runtime


def _engine():
    host = build_runtime()
    context = host.start()
    return host, context.services.require("consultation")


def _closed_state(engine, session_id="audit-case"):
    state = CaseState(session_id=session_id)
    state.phase = Phase.CLOSED
    state.closed_reason = "confirmed_by_user"
    state.risk = RiskAssessment(RiskLevel.ROUTINE)
    state.record(
        Fact(
            key="chief_complaint",
            value="咳嗽两天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence="咳嗽两天",
        )
    )
    return state


class ClosedSessionCorrectionGuardTests(unittest.TestCase):
    """Audit E1: correct_fact/restore_fact bypassed the CLOSED guard and
    could silently reopen a closed case (and orphan its follow-up plan
    once persistence overwrote the file as non-CLOSED)."""

    def test_correct_fact_rejected_on_closed_session(self):
        host, engine = _engine()
        try:
            state, _reply = engine.create_session()
            # Drive the real session closed, then try to correct it.
            state.phase = Phase.CLOSED
            state.closed_reason = "confirmed_by_user"
            with self.assertRaises(ValueError):
                engine.correct_fact(state.session_id, "chief_complaint", "头痛")
        finally:
            host.stop()

    def test_restore_fact_rejected_on_closed_session(self):
        host, engine = _engine()
        try:
            state, _reply = engine.create_session()
            state.phase = Phase.CLOSED
            state.closed_reason = "confirmed_by_user"
            with self.assertRaises(ValueError):
                engine.restore_fact(state.session_id, "chief_complaint")
        finally:
            host.stop()


class FollowUpIdempotencyTests(unittest.TestCase):
    """Audit C1: _schedule_follow_up rebuilt the plan on the confirm
    path, drifting the due clock and wiping recorded outcomes."""

    def test_second_schedule_keeps_first_plan(self):
        host, engine = _engine()
        try:
            state = _closed_state(engine)
            engine._sessions[state.session_id] = state
            first = engine._schedule_follow_up(state)
            self.assertIsNotNone(first)
            second = engine._schedule_follow_up(state)
            self.assertEqual(first["due_at"], second["due_at"])
            scheduled = [
                e for e in engine.events.for_session(state.session_id)
                if e.event_type == "followup.scheduled"
            ]
            self.assertEqual(len(scheduled), 1)
        finally:
            host.stop()


class ConclusionLintScopeTests(unittest.TestCase):
    """Audit C2: the banned-phrase lint scanned patient quotes, so a
    patient writing 「没事」 in a quoted answer silently voided the
    whole conclusion sheet."""

    def test_banned_word_inside_patient_quote_keeps_sheet(self):
        from medagent.conclusion import compose_conclusion

        state = _closed_state(None)
        state.record(
            Fact(
                key="resp_cough_details",
                value="干咳为主",
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id="t1",
                evidence="没事，就是有点咳嗽，干咳为主",
            )
        )
        state.transcript = [
            {"role": "user", "content": "咳嗽两天"},
            {"role": "user", "content": "没事，就是有点咳嗽，干咳为主"},
        ]
        sheet = compose_conclusion(state, summary={})
        self.assertIsNotNone(sheet)
        self.assertIn("disposition", sheet)


class AssertionQuoteTests(unittest.TestCase):
    """Audit E3: assertion-condition quotes were str(fact.value), which
    writes the literal "None" into the evidence chain for ABSENT facts."""

    def test_absent_fact_quote_is_the_denial_text(self):
        from medagent.reasoning import RuleBasedProposer

        state = CaseState(session_id="audit-quote")
        state.record(
            Fact(
                key="chief_complaint",
                value="咳嗽气喘",
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id="t1",
                evidence="咳嗽气喘",
            )
        )
        state.record(
            Fact(
                key="resp_breathing",
                value=None,
                assertion=Assertion.ABSENT,
                source=Source.PATIENT,
                turn_id="t1",
                evidence="没有呼吸困难",
            )
        )
        state.transcript = [
            {"role": "user", "content": "咳嗽气喘"},
            {"role": "user", "content": "没有呼吸困难"},
        ]
        ws = RuleBasedProposer().propose(state)
        lower = ws.hypotheses.get("lower_respiratory")
        self.assertIsNotNone(lower)
        quotes = [ref.quote for ref in lower.evidence_against]
        self.assertNotIn("None", quotes)
        self.assertIn("没有呼吸困难", quotes)


if __name__ == "__main__":
    unittest.main()
