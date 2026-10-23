"""review_summary pathway close-gate (live-eval case 3 follow-up).

Case 3 of the live evaluation: a vague chief complaint （疲劳 + 「肚子
不舒服」) matched no abdominal question under the old keyword triggers,
the model walked the background checklist, and the host summarized the
case closed with a self_care conclusion and abstained directions — the
abdomen was never probed once. Wave 1 widened the triggers and wave 2
opened ask_patient to all registered fields; this gate is the last
backstop: while a pathway triggered by the chief complaint is still
unexplored (detail field never asked/recorded and no differential
evidence on file), review_summary is refused — for model calls (with an
abstain_reason escape hatch that leaves an event trail) and for the
controller's own auto-summary (which asks the detail question instead).
"""

import unittest

from medagent.agent_tools import ProposalError
from medagent.consultation import UserInput
from medagent.domain import Phase
from tests.test_agent import cmd, fact
from tests.test_agent_wave2_engine import make_engine, record

KNOWN = {
    "consult_for": ("self", "我自己"),
    "age_years": (32, "32岁"),
    "emergency_screen": (None, "没有危险信号"),
    "patient_goal": ("想知道要不要紧", "想知道要不要紧"),
}


def background_facts(engine, chief, skip=()):
    """Every applicable blocking field for `chief`, minus `skip`.

    Known fields get real values; everything else is recorded unknown
    (attempted, patient could not say) — the same arrangement the older
    summary tests use, so missing_required ends up containing exactly
    the skipped fields.
    """
    facts = []
    for question in engine.content.questions:
        key = question.fact_key
        if key in skip or not engine.content.question_applies(question, chief):
            continue
        if key == "chief_complaint":
            facts.append(fact(key, chief, chief))
        elif key in KNOWN:
            value, evidence = KNOWN[key]
            assertion = "absent" if key == "emergency_screen" else "present"
            facts.append(fact(key, value, evidence, assertion))
        else:
            facts.append(fact(key, None, "不知道", "unknown"))
    return facts


def text_for(chief):
    return f"我自己，32岁，{chief}，没有危险信号，想知道要不要紧，其它不知道"


class PathwayGateTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def events_of(self, event_type):
        return [
            event
            for event in self.events.for_session(self.state.session_id)
            if event.event_type == event_type
        ]

    def test_case3_shape_is_refused_with_pathway_and_exits_named(self):
        # (a) Chief 「肚子不舒服」 triggers the abdominal pathway; every
        # other applicable field is attempted but gi_details never is.
        chief = "肚子不舒服"
        text = text_for(chief)
        record(
            self.engine,
            self.state,
            text,
            background_facts(self.engine, chief, skip={"gi_details"}),
        )
        self.assertNotIn("gi_details", self.state.facts)
        with self.assertRaises(ProposalError) as caught:
            self.engine._execute(self.state, cmd("review_summary"), text, "t-sum")
        message = str(caught.exception)
        self.assertIn("主诉通路尚未探明", message)
        self.assertIn("abdominal", message)
        self.assertIn("gi_details", message)
        self.assertIn("ask_question/ask_patient", message)
        self.assertIn("abstain_reason", message)
        self.assertNotEqual(self.state.phase, Phase.SUMMARY)

    def test_term_gap_pathway_is_refused_even_with_empty_missing_required(self):
        # 「反酸烧心」 triggers the abdominal pathway but not the
        # gi_details question (narrower catalog terms), so the older
        # missing_required gate sees nothing at all — this gate is the
        # only thing standing between this case and a silent close.
        chief = "反酸烧心"
        text = text_for(chief)
        record(self.engine, self.state, text, background_facts(self.engine, chief))
        context = self.engine._context(self.state, text)
        self.assertEqual(context["missing_required"], [])
        self.assertEqual(
            context["pathway_exploration"],
            [
                {
                    "pathway": "abdominal",
                    "detail_fields": ["gi_details"],
                    "explored": False,
                }
            ],
        )
        with self.assertRaises(ProposalError) as caught:
            self.engine._execute(self.state, cmd("review_summary"), text, "t-sum")
        self.assertIn("abdominal", str(caught.exception))

    def test_abstain_reason_opens_the_gate_and_leaves_an_event(self):
        # (b) Same unexplored case, but the model states why probing is
        # not applicable: the summary proceeds and the abstention is on
        # record with the pathway and the verbatim reason.
        chief = "肚子不舒服"
        text = text_for(chief)
        record(
            self.engine,
            self.state,
            text,
            background_facts(self.engine, chief, skip={"gi_details"}),
        )
        reply = self.engine._execute(
            self.state,
            cmd(
                "review_summary",
                abstain_reason="患者明确拒绝追问腹部细节且无红旗征象",
            ),
            text,
            "t-sum",
        )
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(self.state.phase, Phase.SUMMARY)
        abstentions = self.events_of("review.abstained")
        self.assertEqual(len(abstentions), 1)
        self.assertEqual(abstentions[0].payload["pathways"], ["abdominal"])
        self.assertEqual(abstentions[0].payload["detail_fields"], ["gi_details"])
        self.assertEqual(
            abstentions[0].payload["reason"], "患者明确拒绝追问腹部细节且无红旗征象"
        )

    def test_blank_abstain_reason_is_rejected(self):
        chief = "肚子不舒服"
        text = text_for(chief)
        record(
            self.engine,
            self.state,
            text,
            background_facts(self.engine, chief, skip={"gi_details"}),
        )
        with self.assertRaises(ProposalError) as caught:
            self.engine._execute(
                self.state, cmd("review_summary", abstain_reason="  "), text, "t-sum"
            )
        self.assertIn("abstain_reason", str(caught.exception))

    def test_recorded_detail_field_closes_normally(self):
        # (c) gi_details attempted (recorded unknown): no gate, no
        # abstention event, ordinary summary.
        chief = "肚子不舒服"
        text = text_for(chief)
        record(self.engine, self.state, text, background_facts(self.engine, chief))
        self.assertIn("gi_details", self.state.facts)
        reply = self.engine._execute(self.state, cmd("review_summary"), text, "t-sum")
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(self.events_of("review.abstained"), [])

    def test_chief_without_any_pathway_is_unaffected(self):
        # (d) 「膝盖扭伤」 matches no pathway trigger: no exploration
        # entry is surfaced and the summary is not gated.
        chief = "膝盖扭伤"
        text = text_for(chief)
        record(self.engine, self.state, text, background_facts(self.engine, chief))
        self.assertEqual(self.engine._pathway_exploration(self.state), [])
        self.assertNotIn(
            "pathway_exploration", self.engine._context(self.state, text)
        )
        reply = self.engine._execute(self.state, cmd("review_summary"), text, "t-sum")
        self.assertEqual(reply.kind, "summary")

    def test_already_concluded_case_can_resummarize_after_correction(self):
        # The gate guards the first close only: a case that already
        # produced a conclusion (e.g. a fact-correction replay through
        # correct_fact) re-summarizes without being blocked.
        chief = "肚子不舒服"
        text = text_for(chief)
        record(
            self.engine,
            self.state,
            text,
            background_facts(self.engine, chief, skip={"gi_details"}),
        )
        self.state.conclusion = {"disposition": {"level": "self_care"}}
        reply = self.engine._execute(self.state, cmd("review_summary"), text, "t-sum")
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(self.events_of("review.abstained"), [])

    def test_detail_less_pathway_uses_evidence_not_detail_fields(self):
        # Chest has no dedicated detail field: exploration is judged by
        # proposer evidence alone. A stable trajectory is non-cardiac
        # evidence on file; a worsening one leaves the pathway bare.
        record(
            self.engine,
            self.state,
            "胸闷，变化不大",
            [
                fact("chief_complaint", "胸闷", "胸闷"),
                fact("trajectory", "stable", "变化不大"),
            ],
        )
        self.assertEqual(
            self.engine._pathway_exploration(self.state),
            [{"pathway": "chest", "detail_fields": [], "explored": True}],
        )

        engine, _, _ = make_engine()
        state, _ = engine.create_session()
        record(
            engine,
            state,
            "胸闷，越来越重",
            [
                fact("chief_complaint", "胸闷", "胸闷"),
                fact("trajectory", "worsening", "越来越重"),
            ],
        )
        self.assertEqual(
            engine._pathway_exploration(state),
            [{"pathway": "chest", "detail_fields": [], "explored": False}],
        )


class ControllerAutoCloseGateTests(unittest.TestCase):
    def test_auto_summary_asks_the_detail_question_first(self):
        # End to end through submit(): the model records the background
        # and finally patient_goal; the controller would normally
        # auto-summarize at that point. With the abdominal pathway
        # triggered （反酸烧心） but gi_details never probed, it must ask
        # the detail question instead — and close normally afterwards.
        engine, backend, events = make_engine()
        state, _ = engine.create_session()

        chief = "反酸烧心"
        text1 = "我自己，32岁，反酸烧心一周了，没有危险信号，其它不知道"
        facts = [
            item
            for item in background_facts(engine, chief)
            if item["key"] != "patient_goal"
        ]
        backend.commands = [
            cmd("record_facts", facts=facts),
            cmd("ask_question", question_id="patient_goal"),
        ]
        reply = engine.submit(state.session_id, UserInput(text1))
        self.assertEqual(reply.question_id, "patient_goal")

        backend.commands = [
            cmd(
                "record_facts",
                facts=[fact("patient_goal", "想知道要不要紧", "想知道要不要紧")],
            )
        ]
        reply = engine.submit(
            state.session_id, UserInput("想知道要不要紧", None, reply.action_id)
        )
        self.assertEqual(reply.kind, "question")
        self.assertEqual(reply.question_id, "gi_details")
        self.assertNotEqual(state.phase, Phase.SUMMARY)
        redirects = [
            event
            for event in events.for_session(state.session_id)
            if event.event_type == "review.pathway_gate_redirect"
        ]
        self.assertEqual(len(redirects), 1)
        self.assertEqual(redirects[0].payload["pathway"], "abdominal")
        self.assertEqual(
            [
                event
                for event in events.for_session(state.session_id)
                if event.event_type == "agent.summary_ready"
            ],
            [],
        )

        backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("gi_details", "上腹部，饭后更明显", "在上腹部，饭后更明显")
                ],
            ),
            cmd("review_summary"),
        ]
        reply = engine.submit(
            state.session_id,
            UserInput("在上腹部，饭后更明显", None, reply.action_id),
        )
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(state.phase, Phase.SUMMARY)


if __name__ == "__main__":
    unittest.main()
