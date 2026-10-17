"""Keep a calculation's original input pending until its other content is forwarded."""
from copy import deepcopy
import re

from .domain import Phase, RiskLevel
from .events import Event
from .extensions.conversion_forms import POLICIES


def simple_conversion_request(text):
    """Recognize only a bounded simple request; unfamiliar remainder is retained.

    This is deliberately not a classifier of clinical meaning. It never strips
    the model's quote and assumes that everything outside it is unimportant.
    """
    phrases = {
        "请",
        "麻烦",
        "帮我",
        "给我",
        "我",
        "想",
        "希望",
        "需要",
        "要",
        "能不能",
        "可以",
        "把",
        "将",
        "进行",
        "做",
        "一个",
        "一下",
        "这次",
        "只",
        "仅",
        "先",
        "换算成",
        "换算",
        "换成",
        "转成",
        "转换单位",
        "单位",
        "多少",
        "是多少",
        "成",
        "为",
        "到",
        "和",
        "或",
        "但",
        "还",
        "没有",
        "没",
        "未",
        "不知道",
        "提供",
        "给",
        "具体",
        "数字",
        "数值",
        "读数",
        "填写",
        "让我",
        "吗",
        "呢",
        "血糖",
        "葡萄糖",
        "血液葡萄糖",
        "血浆葡萄糖",
        "血清葡萄糖",
        "blood glucose",
    }
    for policy in POLICIES.values():
        phrases.update(policy["aliases"])
    tokens = "|".join(re.escape(v) for v in sorted(phrases, key=len, reverse=True))
    lexer = re.compile(r"\d{1,12}(?:\.\d{1,12})?|[\s，。；,;！？?!：:→]+|" + tokens)
    position = 0
    while position < len(text):
        match = lexer.match(text, position)
        if match is None:
            return False
        position = match.end()
    return bool(text)


class CalculationFollowupMixin:
    def _retain_calculation_followup(self, state, draft, text, turn_id):
        if simple_conversion_request(text):
            return
        state.calculation_feedback = {
            "id": draft["proposal_id"],
            "capability_id": draft["capability_id"],
            "text": text,
            "source_turn_id": turn_id,
            "outcome": "awaiting_input",
            "processing": False,
            "fact_ids": {k: v.fact_id for k, v in state.facts.items()},
        }
        self.events.append(
            Event(
                state.session_id,
                "capability.supplement_retained",
                self._calculation_followup_context(state),
                turn_id=turn_id,
            )
        )

    @staticmethod
    def _calculation_followup_context(state):
        feedback = state.calculation_feedback
        if feedback is None:
            return None
        return {
            k: deepcopy(feedback[k])
            for k in [
                "id",
                "capability_id",
                "text",
                "source_turn_id",
                "outcome",
                "processing",
            ]
        }

    def _process_calculation_followup(self, state):
        feedback = state.calculation_feedback
        feedback["processing"] = True
        self.events.append(
            Event(
                state.session_id,
                "capability.supplement_forwarded",
                self._calculation_followup_context(state),
                turn_id=feedback["source_turn_id"],
            )
        )
        state.pending_input = feedback["text"]
        return self._run(state, feedback["text"], feedback["source_turn_id"])

    @staticmethod
    def _guard_calculation_followup_updates(state, proposals):
        CalculationFollowupMixin._guard_calculation_followup_keys(
            state, {p["key"] for p in proposals}
        )

    @staticmethod
    def _guard_calculation_followup_keys(state, keys):
        feedback = state.calculation_feedback
        if not feedback or not feedback["processing"]:
            return
        for key in keys:
            previous = state.facts.get(key)
            if (
                previous
                and previous.fact_id != feedback["fact_ids"].get(key)
                and previous.turn_id != feedback["source_turn_id"]
            ):
                raise ValueError("这是较早的补充原话，不能覆盖随后更新的记录。保留当前值，只处理仍未处理的内容。")

    def _project_calculation_followup(self, state, reply):
        feedback = state.calculation_feedback
        if feedback is None:
            return
        closed = state.phase == Phase.CLOSED or state.risk.level in {
            RiskLevel.URGENT,
            RiskLevel.EMERGENCY,
        }
        status = (
            "unprocessed"
            if closed
            else "forwarded"
            if feedback["processing"] and reply.kind != "service_error"
            else "pending"
        )
        reply.calculation_followup = {
            **self._calculation_followup_context(state),
            "status": status,
            "can_process": status == "pending"
            and not feedback["processing"]
            and not state.pending_calculation
            and not state.calculation_resume
            and not state.active_task
            and not state.observation_feedback,
        }
        if reply.kind == "summary" and status == "pending":
            reply.quick_replies = []
            reply.message = "还有同轮补充待处理，处理后再核对摘要。"
        if status != "pending":
            self.events.append(
                Event(
                    state.session_id,
                    "capability.supplement_released",
                    {**self._calculation_followup_context(state), "status": status},
                    turn_id=feedback["source_turn_id"],
                )
            )
            state.calculation_feedback = None
