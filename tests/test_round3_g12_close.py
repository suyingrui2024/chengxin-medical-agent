"""Third-round G-12(d) (W6 package): ask discriminators before an
UNDETERMINED close instead of closing straight into a routine sheet.

G-12 case (round-3 G4, verified against its final.json/transcript):
black stool x2 + dark-red vomit x1 + dizziness/palpitation, the
emergency screen answered unknown ("不确定算不算大出血"), risk
UNDETERMINED, closed routine_visit/48h. The (a)+(b) limbs already
landed as disclosure sentences; this file pins only the (d) limb:
at the close decision, an UNDETERMINED risk with discriminator
questions still owed must detour back to asking instead of closing.

Catalog coverage check (questions.json, read-only for this package):
- 量 (bleeding amount): NO catalog question asks an amount. The
  emergency screen only bundles "大出血?" (yes/no/unknown) and
  gi_details only asks whether 便血 is present. Amount stays a
  reported residual; the re-ask below gives the patient one more
  chance to answer the bundled 大出血 item, it does not elicit an
  amount.
- 频次: gi_details free text (G4 already carries "两次/一次", so
  for G4 this point is already answered, never re-asked).
- 是否持续/复发: trajectory (worsening/improving/stable) is the
  closest catalog proxy, not a direct "still ongoing / recurrent"
  question — partial coverage.
- 头晕心慌是否持续: dizzy_details asks dizziness episode
  duration, but it only applies when the chief complaint itself
  mentions dizziness; G4's chief ("拉黑便、吐暗红色物") does not,
  so for G4 this point has no askable catalog question and stays a
  reported residual.

What the gate does (consultation._plan_next, close fallthrough
only — normal blocking planning still owns the question order while
any blocking candidate remains, and the eventual close still flows
through _finish and the _record_reply close rendering, so the W1
walkthrough is never bypassed):
- never-asked, applicable discriminator questions are selected
  first (helper-level test: in the current candidate semantics
  normal planning already asks never-asked blocking questions
  before close, so this layer is a guard for bypass/amended-chief
  paths, not the active G4 path);
- UNKNOWN-answered emergency_screen, then trajectory, are each
  re-asked at most once per session (session-event guard), so a
  repeated unknown answer still terminates in the original close;
- once no discriminator is owed, the close proceeds exactly as
  before (summary phase, same _finish path, disclosure sentences
  intact). Disposition and risk mapping are untouched: a new
  answer only flows through the engine's normal reassessment.
"""

import unittest

from medagent.domain import Assertion, Fact, Phase, Source
from medagent.events import Event
from medagent.runtime import build_runtime

SCREEN_EVIDENCE = (
    "不确定算不算大出血，我就是拉黑便两次、"
    "吐了一次暗红色的东西，还头晕、心慌。"
)
GI_VALUE = "拉黑便两次、吐暗红色物一次、头晕、心慌"
GOAL = "想知道要不要马上去医院"

G4_ASKED = [
    "sex",
    "age",
    "emergency_screen",
    "trajectory",
    "severity",
    "gi_details",
    "medications_taken",
    "allergies",
    "relevant_history",
    "patient_goal",
]


def _fact(key, value, evidence, assertion=Assertion.PRESENT):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t1",
        evidence=evidence,
    )


def _unknown(key, evidence):
    return _fact(key, None, evidence, Assertion.UNKNOWN)


class G12CloseTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _g4_state(self, asked=None, screen=None, trajectory=None):
        """Round-3 G4 final facts. ``asked`` defaults to the full
        G4 question history; pass a shorter list to leave a
        discriminator never asked."""
        state, _ = self.engine.create_session()
        for item in (
            _fact("consult_for", "self", "我自己咨询"),
            _fact("chief_complaint", "拉黑便、吐暗红色物", "拉黑便、吐暗红色物"),
            _fact("onset", "今天", "今天"),
            _fact("associated_symptoms", "拉黑便、吐暗红色物", "拉黑便、吐暗红色物"),
            _unknown("sex", "不清楚"),
            _unknown("age_years", "不清楚"),
            screen if screen is not None else _unknown("emergency_screen", SCREEN_EVIDENCE),
            trajectory if trajectory is not None else _unknown("trajectory", "变化趋势不清楚"),
            _unknown("severity", "说不清"),
            _fact("gi_details", GI_VALUE, "拉黑便两次"),
            _unknown("medications_taken", "不清楚"),
            _unknown("allergies", "不清楚"),
            _unknown("relevant_history", "不清楚"),
            _fact("patient_goal", GOAL, GOAL),
        ):
            state.record(item)
        state.asked_questions = list(G4_ASKED if asked is None else asked)
        return state

    def test_undetermined_close_reasks_screen_then_trajectory_then_closes(self):
        """G4 replay: close detours to the unknown screen item first,
        then to the unknown trajectory item, then — with both re-asks
        spent and answers still unknown — closes exactly as before
        (summary phase via _finish, no loop, no disposition change)."""
        state = self._g4_state()
        first = self.engine._plan_next(state)
        self.assertEqual(first.kind, "question")
        self.assertEqual(first.question_id, "emergency_screen")
        # Patient again cannot say whether it counts as major bleeding.
        state.record(_unknown("emergency_screen", SCREEN_EVIDENCE))
        second = self.engine._plan_next(state)
        self.assertEqual(second.kind, "question")
        self.assertEqual(second.question_id, "trajectory")
        state.record(_unknown("trajectory", "变化趋势不清楚"))
        third = self.engine._plan_next(state)
        self.assertEqual(third.kind, "summary")
        self.assertEqual(third.phase, Phase.SUMMARY)
        # No fourth detour: the gate is spent, the close stands.
        state.phase = Phase.SUMMARY
        self.assertIsNone(self.engine._undetermined_close_discriminator(state))

    def test_never_asked_discriminator_selected_first(self):
        """Helper-level guard: with gi_details never asked (and its
        fact unrecorded) the gate selects it before any re-ask, even
        though the emergency screen is already unknown-answered."""
        state = self._g4_state(
            asked=[q for q in G4_ASKED if q != "gi_details"]
        )
        state.facts.pop("gi_details")
        selected = self.engine._undetermined_close_discriminator(state)
        self.assertIsNotNone(selected)
        question, reask = selected
        self.assertEqual(question.id, "gi_details")
        self.assertFalse(reask)

    def test_no_askable_discriminator_closes_as_before(self):
        """G4 final facts with both one-time re-asks already spent:
        the close proceeds on the original path (summary phase)."""
        state = self._g4_state()
        for question_id in ("emergency_screen", "trajectory"):
            self.engine.events.append(
                Event(
                    state.session_id,
                    "close.undetermined_discriminator_asked",
                    {"question_id": question_id, "reask": True},
                )
            )
        reply = self.engine._plan_next(state)
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(reply.phase, Phase.SUMMARY)

    def test_gate_does_not_preempt_normal_planning(self):
        """While a blocking question is still outstanding, _plan_next
        asks it through the normal order — the gate only fires at the
        close fallthrough."""
        state = self._g4_state(
            asked=[q for q in G4_ASKED if q != "patient_goal"]
        )
        state.facts.pop("patient_goal")
        reply = self.engine._plan_next(state)
        self.assertEqual(reply.kind, "question")
        self.assertEqual(reply.question_id, "patient_goal")

    def test_gate_does_not_fire_when_screen_negative(self):
        """Screen answered ABSENT (risk ROUTINE, not UNDETERMINED):
        the close proceeds without any discriminator detour."""
        state = self._g4_state(
            screen=_fact(
                "emergency_screen", None, "没有危险信号", Assertion.ABSENT
            )
        )
        self.assertIsNone(self.engine._undetermined_close_discriminator(state))
        reply = self.engine._plan_next(state)
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(reply.phase, Phase.SUMMARY)

    def test_present_detail_is_never_reasked(self):
        """gi_details is PRESENT in G4 (it already carries frequency),
        so even with the screen re-ask spent the gate moves to
        trajectory — never back to the answered detail question."""
        state = self._g4_state()
        self.engine.events.append(
            Event(
                state.session_id,
                "close.undetermined_discriminator_asked",
                {"question_id": "emergency_screen", "reask": True},
            )
        )
        reply = self.engine._plan_next(state)
        self.assertEqual(reply.kind, "question")
        self.assertEqual(reply.question_id, "trajectory")


if __name__ == "__main__":
    unittest.main()
