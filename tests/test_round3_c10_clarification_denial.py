"""Round-3 C-10 (P1, cardiology C3): denied clarification must have memory.

Third-round transcript (disease-round3/心内科/C3-劳力相关胸闷):
turn 1 chief complaint -> turn 2 clarification (denied) -> turn 5
emergency screening (denied) -> turn 8 paraphrase of the same complaint
("快走一段路就胸闷，停下来歇会儿就好") -> turn 9 the SAME whole
clarification round again (denied again). The fix lives in the adaptive
engine's own call sites (src/medagent/agent.py): before a clarification
is delivered, the turn's bare-signal labels are checked against denied
clarification history; a same-group paraphrase falls through to normal
consultation instead of re-asking the whole group. The clarification
gate itself (consultation.py) is untouched.

Covered here:
- the C-10 sequence end to end (no second clarification round, session
  stays open, normal question delivered);
- a definite hit after a denial still takes the emergency path
  (suppression applies only to the "clarify" gate outcome, never to
  definite signals);
- label scoping of the memory helper (same group suppressed, new group
  still clarifiable, legacy label-less denial records still suppress).
"""

import unittest

from medagent.consultation import UserInput
from medagent.domain import Phase
from tests.test_agent import cmd, fact
from tests.test_agent_wave2_engine import make_engine

CHIEF = "我自己咨询。我快走一段路就胸闷，停下来歇会儿就好。开始大概近一个月了。"
PARAPHRASE = "快走一段路就胸闷，停下来歇会儿就好。"
COMBO = "我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心"
CHEST_LABEL = "高风险胸痛描述"


def _asked_count(events, session_id):
    return len(
        [
            event
            for event in events.for_session(session_id)
            if event.event_type == "emergency.clarification_asked"
        ]
    )


class DeniedClarificationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_denied_clarification_not_reasked_on_paraphrase(self):
        # Turn 1: bare chest location x signal co-occurrence -> clarification.
        reply = self.engine.submit(self.state.session_id, UserInput(CHIEF))
        self.assertEqual(reply.kind, "clarification")
        self.assertEqual(
            _asked_count(self.events, self.state.session_id), 1
        )
        # Turn 2 (denial): the model round that follows records the chief
        # facts (quoting the clarified first turn, per the P5 denial
        # exception) and asks the next question in ranker order.
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact(
                        "chief_complaint",
                        "快走时胸闷，休息后缓解",
                        "快走一段路就胸闷，停下来歇会儿就好",
                    ),
                    fact("onset", "近一个月", "近一个月"),
                ],
            ),
            cmd("ask_question", question_id="sex"),
        ]
        reply = self.engine.submit(
            self.state.session_id,
            UserInput("没有，我没有这种情况", "deny_emergency_clarification"),
        )
        self.assertEqual(reply.kind, "question")
        self.assertEqual(reply.question_id, "sex")
        # Turn 3: paraphrase of the same complaint. Pre-fix this delivered
        # a second, identical clarification round (asked count 2). Post-fix
        # it is a normal consultation turn: the paraphrase is recorded
        # and the next question is delivered, with no second clarification.
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact(
                        "associated_symptoms",
                        "劳力诱发胸闷、休息缓解",
                        PARAPHRASE,
                    ),
                ],
            ),
            cmd("ask_question", question_id="sex"),
        ]
        reply = self.engine.submit(self.state.session_id, UserInput(PARAPHRASE))
        self.assertEqual(reply.kind, "question")
        self.assertNotEqual(reply.kind, "clarification")
        self.assertNotEqual(self.state.phase, Phase.CLOSED)
        self.assertEqual(
            _asked_count(self.events, self.state.session_id), 1
        )
        self.assertIn("associated_symptoms", self.state.facts)

    def test_definite_hit_after_denial_still_takes_emergency_path(self):
        self.engine.submit(self.state.session_id, UserInput(COMBO))
        # Denial turn needs no scripted model round for this test: the
        # denial branch runs before model invocation, so the denial is
        # recorded even though the empty backend yields service_error.
        self.engine.submit(
            self.state.session_id,
            UserInput("没有，我没有这种情况", "deny_emergency_clarification"),
        )
        self.assertIsNotNone(
            self.state.runtime_snapshot.get("emergency_clarification_denied")
        )
        # A definite (non-bare) signal is not a "clarify" gate outcome,
        # so denial memory must not stand it down.
        reply = self.engine.submit(
            self.state.session_id, UserInput("他现在叫不醒")
        )
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(self.state.phase, Phase.CLOSED)
        self.assertIn("120", reply.message)


class DeniedBeforeHelperTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_no_denial_record_is_not_suppressed(self):
        self.assertFalse(
            self.engine._emergency_clarification_denied_before(
                self.state, PARAPHRASE
            )
        )

    def test_same_group_denial_is_suppressed(self):
        self.state.runtime_snapshot["emergency_clarification_denied"] = {
            "text": CHIEF,
            "for_input": "没有，我没有这种情况",
            "labels": [CHEST_LABEL],
        }
        self.assertTrue(
            self.engine._emergency_clarification_denied_before(
                self.state, PARAPHRASE
            )
        )

    def test_new_group_denial_is_not_suppressed(self):
        # A denial for a different label set must not suppress a
        # clarification for a new group (future combo content).
        self.state.runtime_snapshot["emergency_clarification_denied"] = {
            "text": "别处的不适",
            "for_input": "没有，我没有这种情况",
            "labels": ["其他危险信号组"],
        }
        self.assertFalse(
            self.engine._emergency_clarification_denied_before(
                self.state, PARAPHRASE
            )
        )

    def test_accumulated_history_covers_earlier_denials(self):
        # The latest-denial dict alone would forget the first group;
        # the accumulated list must still suppress it.
        self.state.runtime_snapshot["emergency_clarification_denied"] = {
            "text": "别处的不适",
            "for_input": "没有，我没有这种情况",
            "labels": ["其他危险信号组"],
        }
        self.state.runtime_snapshot[
            "emergency_clarification_denied_labels"
        ] = [["其他危险信号组"], [CHEST_LABEL]]
        self.assertTrue(
            self.engine._emergency_clarification_denied_before(
                self.state, PARAPHRASE
            )
        )
        self.state.runtime_snapshot[
            "emergency_clarification_denied_labels"
        ] = [["其他危险信号组"]]
        self.assertFalse(
            self.engine._emergency_clarification_denied_before(
                self.state, PARAPHRASE
            )
        )

    def test_legacy_denial_without_labels_still_suppresses(self):
        self.state.runtime_snapshot["emergency_clarification_denied"] = {
            "text": CHIEF,
            "for_input": "没有，我没有这种情况",
        }
        self.assertTrue(
            self.engine._emergency_clarification_denied_before(
                self.state, PARAPHRASE
            )
        )


if __name__ == "__main__":
    unittest.main()
