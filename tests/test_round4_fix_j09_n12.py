"""Fourth-round fix batch (baseline 64682d5): J-09 and N-12 only.

J-09 (P2, close-expression defect): the visit-part residual walk
listed meaningless verb fragments as their own unaddressed parts,
so a J1/J2 close gained a redundant fixed sentence ("关于‘看看’" /
"关于‘一下’"). The residual filter must drop fragments with no
substantive request while keeping real residuals (Y-16 挑开,
D-14 开药) and pure visit parts covered.

N-12 (P2, safety-net wording asymmetry): the third-round denial
extraction scanned CaseState facts only, but in live N3 the denial
("没有肢体无力说话不清") never reached any fact's value/evidence
— the recording layer books only the affirmative part — so the
dizziness safety-net item "言语不清" stayed unqualified while its
neighbours were qualified. The denial scan must also cover raw
patient answer text in state.transcript (user turns only).

No other behaviour is pinned or changed here: disposition,
ordering, thresholds, and direction/department shadow gates are
untouched by either fix.
"""
from __future__ import annotations

import unittest

from medagent import closing
from medagent.conclusion import (
    _GOAL_PART_FIXED_HANDLING,
    _visit_part_residual,
    compose_conclusion,
    unaddressed_goal_parts,
)
from medagent.domain import (
    AgentReply,
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)

FIXED = _GOAL_PART_FIXED_HANDLING
VISIBLE = "先说结论：暂时不需要专门跑一趟医院。"


class J09ResidualFilterTests(unittest.TestCase):
    def test_meaningless_residuals_are_not_parts(self):
        # J1/J2 original goal wording, verbatim from round 4.
        self.assertEqual(_visit_part_residual("想知道要不要去医院看看"), "")
        self.assertEqual(_visit_part_residual("要不要去医院检查一下"), "")
        # Direct variants of the same defect shape.
        self.assertEqual(_visit_part_residual("要不要去医院看一下"), "")
        self.assertEqual(_visit_part_residual("要不要去医院看一看病"), "")
        self.assertEqual(_visit_part_residual("要不要去医院顺便看看"), "")
        self.assertEqual(_visit_part_residual("要不要去医院试试"), "")

    def test_substantive_residuals_and_pure_visit_parts_unchanged(self):
        # Pinned nails: Y-16 / D-14 residuals still become parts.
        self.assertEqual(_visit_part_residual("想知道要不要去医院挑开"), "挑开")
        self.assertEqual(_visit_part_residual("要不要去医院开药"), "开药")
        # A substantive residual keeps its content past a complement.
        self.assertEqual(_visit_part_residual("要不要去医院挑开一下"), "挑开")
        # Pure visit parts stay covered, with no residual.
        self.assertEqual(_visit_part_residual("要不要去医院"), "")
        self.assertEqual(_visit_part_residual("要不要去医院检查"), "")
        self.assertEqual(_visit_part_residual("想整理就医记录"), "")

    def test_j1_j2_goal_lines_list_only_the_substantive_part(self):
        self.assertEqual(
            unaddressed_goal_parts(
                "想知道要不要去医院看看，平时怎么能睡好一点。", VISIBLE
            ),
            ["平时怎么能睡好一点"],
        )
        self.assertEqual(
            unaddressed_goal_parts(
                "想知道要不要去医院检查一下，平时怎么缓解紧张。", VISIBLE
            ),
            ["平时怎么缓解紧张"],
        )

    def _close(self, goal_line):
        state = CaseState(session_id="round4-j09")
        reply = AgentReply(VISIBLE, Phase.CLOSED, kind="closed")
        sheet = {
            "goal_line": goal_line,
            "self_care": {
                "do": ["多休息，避免劳累", "适量喝水", "把症状变化记下来，就诊时告诉医生"],
                "avoid": ["不要自行服用抗生素"],
                "measure": [],
            },
        }
        closing._append_unaddressed_goals(state, reply, sheet, VISIBLE)
        return reply.message

    def test_j1_j2_close_has_no_residual_sentence(self):
        # The substantive part still gets its one sentence; the
        # close is otherwise byte-identical to base + that sentence.
        self.assertEqual(
            self._close("想知道要不要去医院看看，平时怎么能睡好一点。"),
            VISIBLE + f"关于‘平时怎么能睡好一点’：{FIXED}。",
        )
        self.assertEqual(
            self._close("想知道要不要去医院检查一下，平时怎么缓解紧张。"),
            VISIBLE + f"关于‘平时怎么缓解紧张’：{FIXED}。",
        )
        for message in (
            self._close("想知道要不要去医院看看，平时怎么能睡好一点。"),
            self._close("想知道要不要去医院检查一下，平时怎么缓解紧张。"),
        ):
            self.assertNotIn("关于‘看看’", message)
            self.assertNotIn("关于‘一下’", message)


def _n12_state(denial_turns=(), assistant_denial=False):
    """Live N3 shape: facts carry ONLY affirmative parts; the
    denial ("没有肢体无力说话不清") lives only in raw patient
    answer text, i.e. transcript user turns."""
    state = CaseState(session_id="round4-n12")
    state.phase = Phase.CLOSED
    state.closed_reason = "confirmed_by_user"
    state.risk = RiskAssessment(RiskLevel.ROUTINE)
    chief = "我头晕，一转头就天旋地转。"
    state.record(
        Fact(
            key="chief_complaint",
            value=chief,
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=chief,
        )
    )
    state.transcript.append({"role": "user", "content": chief})
    state.record(
        Fact(
            key="dizzy_details",
            value="天旋地转，每次不到一分钟，和翻身有关",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t8",
            evidence="天旋地转，每次不到一分钟，和翻身有关",
        )
    )
    state.record(
        Fact(
            key="associated_symptoms",
            value="恶心想吐",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t8",
            evidence="恶心想吐",
        )
    )
    state.transcript.append(
        {"role": "user", "content": "天旋地转不到一分钟、恶心想吐。"}
    )
    for turn in denial_turns:
        state.transcript.append({"role": "user", "content": turn})
    if assistant_denial:
        state.transcript.append(
            {"role": "assistant", "content": "没有肢体无力说话不清。"}
        )
    return state


N3_DENIAL_TURNS = (
    "没有耳鸣听力下降、没有肢体无力说话不清、没有头疼。",
    "天旋地转，每次不到一分钟，和翻身有关。没有耳鸣听力下降，"
    "没有肢体无力说话不清，走路不稳不清楚。",
)


class N12TranscriptDenialTests(unittest.TestCase):
    def test_denial_in_transcript_only_qualifies_speech_item(self):
        control = compose_conclusion(_n12_state(), None)
        self.assertEqual(
            control["return_now_if"],
            ["言语不清", "肢体无力新出现", "走路不稳明显加重"],
        )
        denied = compose_conclusion(_n12_state(N3_DENIAL_TURNS), None)
        # Only the denied item gains the qualifier; disposition and
        # every other item are unchanged (fail-safe narrowing).
        self.assertEqual(
            denied["return_now_if"],
            ["言语不清新出现", "肢体无力新出现", "走路不稳明显加重"],
        )
        self.assertEqual(denied["disposition"], control["disposition"])

    def test_assistant_turn_denial_does_not_narrow(self):
        # The system's own wording must not narrow its own safety
        # net: only user turns are scanned.
        sheet = compose_conclusion(_n12_state(assistant_denial=True), None)
        self.assertEqual(
            sheet["return_now_if"],
            ["言语不清", "肢体无力新出现", "走路不稳明显加重"],
        )
