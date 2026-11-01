"""Mixin: pathway for AdaptiveConsultationEngine."""
from ._preamble import *  # noqa: F401,F403


class AdaptiveConsultationEnginePathway:
    def _pathway_exploration(self, state: CaseState):
        """Per triggered pathway: were its details actually probed?

        Triggering reuses the reasoning core's rule (chief complaint
        contains an unnegated pathway term). A triggered pathway counts
        as explored when any of these holds:
        - one of its detail facts is recorded (any assertion — being
          asked and answered "不知道" is a probe, same semantics as
          missing_required), or its detail question was asked at all;
        - every detail question is excluded by known facts (an age/sex
          gate), so nothing askable remains;
        - the rule proposer, run over current facts, holds at least one
          evidence entry for one of the pathway's hypotheses that rests
          on a fact other than the chief complaint itself (this is the
          only signal for pathways with no dedicated detail field, e.g.
          chest, and a backstop for evidence gathered via other fields).

        Testability note (healthcheck P2-A-D5): the chest gate is
        submit-unreachable by design — a chest complaint is intercepted
        by the emergency triage in _submit before _run ever sees it, and
        that precedence must not be weakened. Its gate behaviour is
        therefore verified by calling _execute(review_summary) directly
        on a constructed state (see test_pathway_close_gate.py), never
        by routing a chest complaint through submit().
        """
        chief = str(state.get("chief_complaint", ""))
        if not chief:
            return []
        workspace = self._pathway_proposer.propose(state)
        exploration = []
        for pathway in self._pathways:
            terms = pathway.get("chief_complaint_contains_any", [])
            if not any(contains_unnegated(chief, term) for term in terms):
                continue
            detail_facts = self._pathway_detail_facts.get(pathway["id"], [])
            explored = any(
                fact_key in state.facts
                or self._questions_by_fact[fact_key].id in state.asked_questions
                for fact_key in detail_facts
            )
            if not explored and detail_facts:
                explored = all(
                    not self._fact_gates_pass(
                        state, self._questions_by_fact[fact_key]
                    )
                    for fact_key in detail_facts
                )
            if not explored:
                explored = any(
                    ref.fact_key != "chief_complaint"
                    for raw in pathway.get("hypotheses", [])
                    if (hypothesis := workspace.hypotheses.get(raw["id"]))
                    is not None
                    for ref in [
                        *hypothesis.evidence_for,
                        *hypothesis.evidence_against,
                    ]
                )
            exploration.append(
                {
                    "pathway": pathway["id"],
                    "detail_fields": detail_facts,
                    "explored": explored,
                }
            )
        return exploration

    @staticmethod
    def _unexplored_pathway_error(unexplored) -> ProposalError:
        parts = []
        for entry in unexplored:
            if entry["detail_fields"]:
                fields = "、".join(entry["detail_fields"])
            else:
                fields = "该通路无专属细节字段，需伴随症状、病史等鉴别证据落账"
            parts.append(f"{entry['pathway']} 通路（细节字段：{fields}）")
        return ProposalError(
            "主诉通路尚未探明，不能直接核对摘要："
            + "；".join(parts)
            + "。请先用 ask_question/ask_patient 探明对应细节字段；"
            "若临床上确不适用，重新调用 review_summary 并填写 abstain_reason 说明理由。"
        )

    def _pathway_gate_redirect(self, state: CaseState, turn_id: str):
        """Controller close-gate for the host-driven auto-summary.

        Mirrors the review_summary tool gate: instead of summarizing an
        unexplored triggered pathway away, ask its first unrecorded,
        still-eligible detail question directly. _ask registers the
        question as asked, so a patient who cannot elaborate further is
        not asked in a loop — the next close attempt sees the pathway as
        attempted. Pathways without a detail field cannot be probed
        deterministically here and are left to the tool gate and the
        triage layers.
        """
        for entry in self._pathway_exploration(state):
            if entry["explored"]:
                continue
            for fact_key in entry["detail_fields"]:
                if fact_key in state.facts:
                    continue
                question = self._questions_by_fact[fact_key]
                if not self._fact_gates_pass(state, question):
                    continue
                self.events.append(
                    Event(
                        state.session_id,
                        "review.pathway_gate_redirect",
                        {"pathway": entry["pathway"], "detail_field": fact_key},
                        turn_id=turn_id,
                    )
                )
                return self._ask(
                    state,
                    question,
                    "主诉通路尚未探明，控制器在摘要前先补问其细节字段",
                )
        return None

    def _controller_summary(self, state, text, turn_id):
        """Controller auto-summary with a fallback that never kills the
        session (C-06/J-02).

        The model-called review_summary path already survives a gate
        rejection (the error returns as a tool result and the model can
        abstain); the controller's own auto-summary used to let the
        ProposalError propagate out of _run and strand the session with
        no conclusion. Order: detail-question redirect, then the plain
        summary, then one discrimination probe for detail-less pathways
        (chest: associated symptoms / relevant history — no new chest
        detail question is invented, per the batch A scope), then an
        abstain re-call when probing is genuinely inapplicable, then a
        last-resort return to planning. A ProposalError must never
        reach the driver from here.
        """
        redirect = self._pathway_gate_redirect(state, turn_id)
        if redirect is not None:
            return redirect
        try:
            return self._execute(
                state, {"tool": "review_summary", "arguments": {}}, text, turn_id
            )
        except ProposalError:
            pass
        for fact_key in ("associated_symptoms", "relevant_history"):
            if fact_key in state.facts:
                continue
            question = self._questions_by_fact.get(fact_key)
            if question is None or question.id in state.asked_questions:
                continue
            if not self.content.question_applies_in_state(question, state):
                continue
            if not self._fact_gates_pass(state, question):
                continue
            self.events.append(
                Event(
                    state.session_id,
                    "review.pathway_discrimination_redirect",
                    {"detail_field": fact_key},
                    turn_id=turn_id,
                )
            )
            return self._ask(
                state,
                question,
                "主诉通路尚未探明，控制器在摘要前先补问鉴别字段",
            )
        try:
            return self._execute(
                state,
                {
                    "tool": "review_summary",
                    "arguments": {
                        "abstain_reason": (
                            "该通路无专属细节字段，已记录的伴随症状、病史等"
                            "鉴别证据不足以单独探明，摘要前不再追问该通路细节；"
                            "结论与随访仍按已记录事实生成。"
                        )
                    },
                },
                text,
                turn_id,
            )
        except ProposalError:
            pass
        if state.self_check_conversation:
            try:
                return self._resume_task_conversation(state)
            except ValueError:
                # A resume that fails (e.g. the task's basis changed and
                # the frame was cleared by the resume itself) must fall
                # back to planning, never escape to the driver. Note: a
                # still-blocked frame can raise again inside _plan_next;
                # that path keeps the block visible rather than silently
                # dropping the suspended step.
                pass
        self.events.append(
            Event(
                state.session_id,
                "review.controller_fallback_exhausted",
                {},
                turn_id=turn_id,
            )
        )
        return self._plan_next(state)
