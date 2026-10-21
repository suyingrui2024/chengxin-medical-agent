"""Deterministic gate for replies before they become patient-visible."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain import AgentReply, CaseState, Phase, QuickReply, RiskLevel
from .adaptive_questions import AdaptiveQuestionError, validate_adaptive_question


@dataclass(frozen=True)
class PublicationDecision:
    allowed: bool
    rule_ids: tuple[str, ...]


class ReplyPublicationGate:
    """Validate delivery invariants without asking another model to judge safety."""

    _EMERGENCY_TEXT = "请现在拨打 120，或让身边的人协助呼叫急救；不要自己驾车。不要继续等待本次对话。"

    def validate(self, state: CaseState, reply: AgentReply) -> PublicationDecision:
        failures: list[str] = []
        if reply.phase != state.phase:
            failures.append("reply_phase_matches_case")
        if not isinstance(reply.message, str) or not reply.message.strip() or len(reply.message) > 4000:
            failures.append("reply_message_is_bounded_text")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in reply.message):
            failures.append("reply_message_has_no_control_characters")
        choices = reply.quick_replies
        if not isinstance(choices, list) or any(
            not isinstance(choice, QuickReply)
            or not choice.label.strip()
            or not choice.value.strip()
            or len(choice.label) > 160
            or len(choice.value) > 200
            for choice in choices
        ):
            failures.append("quick_replies_are_bounded")
        if len({choice.value for choice in choices}) != len(choices):
            failures.append("quick_reply_values_are_unique")
        if reply.adaptive_question is not None:
            try:
                validate_adaptive_question(reply.adaptive_question)
            except AdaptiveQuestionError:
                failures.append("adaptive_question_is_valid")
            if reply.kind != "question" or reply.task or reply.quick_replies:
                failures.append("adaptive_question_has_one_interaction")
        if state.risk.level == RiskLevel.EMERGENCY and (
            reply.kind != "emergency"
            or reply.phase != Phase.CLOSED
            or "120" not in reply.message
        ):
            failures.append("emergency_reply_is_controller_owned")
        if state.risk.level == RiskLevel.URGENT and reply.kind not in {
            "urgent",
            "emergency",
        }:
            failures.append("urgent_reply_is_controller_owned")
        if reply.kind == "emergency" and reply.phase != Phase.CLOSED:
            failures.append("emergency_reply_closes_case")
        if reply.kind in {"question", "summary"} and reply.phase == Phase.CLOSED:
            failures.append("closed_case_has_no_open_question")
        return PublicationDecision(not failures, tuple(failures))

    def fallback(self, state: CaseState, decision: PublicationDecision) -> AgentReply:
        """Return a fixed safe response when a reply violates the delivery contract."""
        if state.risk.level == RiskLevel.EMERGENCY:
            return AgentReply(
                self._EMERGENCY_TEXT,
                Phase.CLOSED,
                kind="emergency",
                summary={"care_route": "立即急救", "triggers": state.risk.triggers},
                developer_trace={"publication_gate": list(decision.rule_ids)},
            )
        return AgentReply(
            "当前回复未通过安全检查。已保留已记录的信息；请查看当前记录、补充必要信息，或稍后重试。",
            state.phase,
            kind="publication_blocked",
            developer_trace={"publication_gate": list(decision.rule_ids)},
        )
