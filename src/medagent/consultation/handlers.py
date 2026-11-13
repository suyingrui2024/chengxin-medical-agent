"""Workflow component: handlers for ConsultationEngine."""
import re
from uuid import uuid4
from ..clinical.interpreter import Interpretation
from ..core.domain import (
    AgentReply,
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskLevel,
    Source,
)
from ..core.events import Event
from ..core.turn_context import TurnContext
from ..core.worry import extract_worry_object
from .vocabulary import is_refusal_text, is_summary_confirmation_text
from .types import StaleActionError, UserInput


#: 明确在"要一个算出来的数"的说法。规则引擎没有计算能力（计算挂在自适应
#: 引擎的工具上，见 extensions 的公式包），所以这类请求既算不出来，也不该
#: 被当成当前题的答案悄悄记下——患者会以为系统算过了。
_CALCULATION_OBJECTS = (
    "bmi",
    "体重指数",
    "身高",
    "体重",
    "血压",
    "平均动脉压",
    "心率",
    "血糖",
    "肌酐",
    "egfr",
    "剂量",
    "孕周",
    "预产期",
    "体表面积",
)
_CALCULATION_ASK = re.compile(
    # 要么是"帮我算…"这种请求，要么是"算一下/换算"这种动作；
    # 单说"我算过了"不算请求，避免把"体重我算过了，70公斤"当计算请求。
    r"(?:帮我|帮忙|给我|替我)(?:算|计算|换算)|算一算|算一下|算算|计算一下|计算|换算"
)
_DIGIT = re.compile(r"\d")


def _asks_for_a_calculation(text: str) -> bool:
    """True when the patient is asking for a number we would have to compute.

    Narrow on purpose: the ask has to name a computed quantity and carry a
    number.  "我算了一下大概38度" is a hedged temperature, not a request.
    """
    lowered = text.lower()
    if not _DIGIT.search(text):
        return False
    if not _CALCULATION_ASK.search(text):
        return False
    return any(name in lowered for name in _CALCULATION_OBJECTS)


class ConsultationEngineHandlers:

    def __init__(self, engine):
        self.engine = engine
        self._turn_pipeline = (
            ("location_revision", self._h_location_revision),
            ("closed_session", self._h_closed_session),
            ("subject_control", self._h_subject_control),
            ("prepared_location", self._h_prepared_location),
            ("observation_action", self._h_observation_action),
            ("summary_review", self._h_summary_review),
            ("intake", self._h_intake),
            ("risk_escalation", self._h_risk_escalation),
            ("planning", self._h_planning),
        )

    def _submit(self, state: CaseState, user_input: UserInput) -> AgentReply:
        """Take a patient turn on the deterministic engine.

        The order here is the contract.  A request for priority help comes
        first, then the stale-action check, then the turn context is built --
        and only then is a pending emergency clarification answered.  So a
        confirmation or a denial is read against the state the turn actually
        produced, not against the state before the patient's words.
        """
        if user_input.choice_value == "request_emergency_help":
            return self.engine.subjects._requested_emergency_help(state, user_input.text)
        self._guard_stale_action(state, user_input)
        ctx = self._begin_turn(state, user_input)
        if user_input.choice_value == "confirm_emergency_clarification":
            confirmed = self.engine.emergency._confirm_emergency_clarification(state, ctx.turn_id)
            if confirmed is not None:
                self.engine.replies._record_reply(
                    state, confirmed, ctx.turn_id, "鉴别澄清已确认，走急症路径"
                )
                return confirmed
        if user_input.choice_value == "deny_emergency_clarification":
            if self.engine.emergency._emergency_clarification_pending(state) is not None:
                state.runtime_snapshot.pop("emergency_clarification", None)
                self.engine.events.append(
                    Event(
                        state.session_id,
                        "emergency.clarification_denied",
                        {},
                        turn_id=ctx.turn_id,
                    )
                )
        direct = self._direct_emergency(state, ctx)
        if direct is not None:
            return direct
        # Deliberately outside the pipeline registry (whose handler
        # order is pinned by test_turn_pipeline): a confirmed summary
        # must reach the conclusion flow before any pipeline handler
        # (observation, self-check intake) can re-suspend it.
        confirmed = self._early_summary_confirm(state, user_input, ctx)
        if confirmed is not None:
            self.engine.replies._record_reply(
                state, confirmed, ctx.turn_id, "摘要已确认，进入结论流程"
            )
            return confirmed
        for name, handler in self._turn_pipeline:
            ctx.trace.append(name)
            reply = handler(state, user_input, ctx)
            if reply is not None:
                self.engine.replies._record_reply(state, reply, ctx.turn_id, ctx.reply_reason)
                return reply
        raise RuntimeError("turn pipeline exhausted without producing a reply")

    def _guard_stale_action(self, state: CaseState, user_input: UserInput) -> None:
        current_action = f"{state.session_id}:{state.revision}"
        if (
            user_input.expected_action_id
            and user_input.expected_action_id != current_action
            and not self.engine.triage.scan_text(self.engine.tasks._location_input_text(user_input))
        ):
            self.engine.events.append(
                Event(
                    state.session_id,
                    "turn.rejected",
                    {
                        "reason": "stale_action",
                        "expected": user_input.expected_action_id,
                        "current": current_action,
                    },
                )
            )
            raise StaleActionError("页面上的问题已经变化，请按当前问题重新回答")

    def _begin_turn(self, state: CaseState, user_input: UserInput) -> TurnContext:
        """Open a turn: settle the text, prepare the location answer, record it.

        The text is settled before anything else uses it.  A location input
        wins over the typed text, and a prepared location answer wins over
        both -- otherwise the location the patient selected would be parsed as
        prose.

        The three control checks run only when the text is not itself a danger
        signal, and they run before the turn is recorded, so a control the
        patient triggered is part of the same turn.
        """
        turn_id = str(uuid4())
        text = user_input.text.strip()
        urgent_text = self.engine.tasks._location_input_text(user_input)
        if self.engine.triage.scan_text(urgent_text):
            text = urgent_text
        prepared_location = None
        if not self.engine.triage.scan_text(text):
            prepared_location = self.engine.tasks._prepare_location_answer(state, user_input)
            if prepared_location:
                text = prepared_location["text"]
            self.engine.subjects._check_subject_action(state, user_input)
            self.engine.observations._check_observation_action(state, user_input)
            self.engine.locations._check_location_revision_action(state, user_input)
        self.engine.events.append(
            Event(
                state.session_id,
                "turn.user_message",
                {"text": text, "choice_value": user_input.choice_value},
                turn_id=turn_id,
            )
        )
        self._capture_worry(state, text, turn_id)
        return TurnContext(
            turn_id=turn_id, text=text, prepared_location=prepared_location
        )

    def _capture_worry(self, state: CaseState, text: str, turn_id: str) -> None:
        """Record the session's first patient-named worry (batch B, WORRY).

        Deterministic and model-independent: the first worry sentence a
        patient utters lands as its own patient_worry fact, never into
        patient_goal (K-03: a worry sentence must not overwrite the
        goal). First-wins: once recorded, later worries do not replace
        it. This only records a fact — no triage, question-selection,
        or disposition logic reads patient_worry, so no model or triage
        decision can change because of it. Both engines call this
        helper from their own user-turn entry (the deterministic engine
        via _begin_turn, the adaptive engine via its own _submit), so
        disease tests running the adaptive engine are covered too.
        """
        if not text or "patient_worry" in state.facts:
            return
        obj = extract_worry_object(text)
        if not obj:
            return
        state.record(
            Fact(
                key="patient_worry",
                value=obj,
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id=turn_id,
                evidence=text.strip(),
            )
        )
        self.engine.events.append(
            Event(
                state.session_id,
                "worry.captured",
                {"object": obj},
                turn_id=turn_id,
            )
        )

    def _direct_emergency(self, state: CaseState, ctx: TurnContext):
        direct_triggers = self.engine.triage.scan_text(ctx.text)
        if not direct_triggers:
            return None
        action = self.engine.emergency._emergency_clarification_action(state, ctx.text, ())
        if action == "clarify":
            reply = self.engine.emergency._emergency_clarification_reply(
                state, ctx.text, ctx.turn_id
            )
            self.engine.replies._record_reply(state, reply, ctx.turn_id, "急症信号仅为裸共现，先鉴别澄清")
            return reply
        state.risk = self.engine.triage.assess(state, ctx.text)
        if action == "confirmed":
            reply = self.engine.emergency._emergency_reply(
                state, state.risk.triggers, include_clarification=True
            )
            self.engine.replies._record_reply(state, reply, ctx.turn_id, "鉴别澄清已确认，走急症路径")
            return reply
        reply = self.engine.emergency._emergency_reply(state, direct_triggers)
        self.engine.replies._record_reply(state, reply, ctx.turn_id, "原始输入命中确定性急症门")
        return reply

    def _h_location_revision(self, state, user_input, ctx):
        revision_reply = self.engine.locations._handle_location_revision_action(
            state, user_input, ctx.text, ctx.turn_id
        )
        if revision_reply is not None:
            ctx.reply_reason = "定位修改与原问诊接续"
        return revision_reply

    def _h_closed_session(self, state, user_input, ctx):
        if state.phase != Phase.CLOSED:
            return None
        reply = (
            self.engine.emergency._emergency_reply(state, state.risk.triggers)
            if state.closed_reason == "emergency_route"
            else AgentReply(
                "这次问诊已经结束。如情况有变化，请开始一次新的问诊；出现紧急情况请立即拨打 120。",
                Phase.CLOSED,
                kind="closed",
            )
        )
        ctx.reply_reason = "会话已关闭"
        return reply

    def _h_subject_control(self, state, user_input, ctx):
        subject_reply = self.engine.subjects._subject_control(state, user_input, ctx.text, ctx.turn_id)
        if subject_reply is not None:
            ctx.reply_reason = "就诊对象边界与原问诊接续"
        return subject_reply

    def _h_prepared_location(self, state, user_input, ctx):
        if not ctx.prepared_location:
            return None
        revised = self.engine.locations._complete_location_revision(
            state, ctx.prepared_location, ctx.turn_id
        )
        if revised is None:
            self.engine.tasks._record_location_answer(state, ctx.prepared_location, ctx.turn_id)
            revised = self.engine.planning._plan_next(state)
        ctx.reply_reason = "结构化定位答案已核对"
        return revised

    def _h_observation_action(self, state, user_input, ctx):
        observation_reply = self.engine.observations._handle_observation_action(
            state, user_input, ctx.text, ctx.turn_id
        )
        if observation_reply is not None:
            ctx.reply_reason = "重复观察及原问诊接续"
        return observation_reply

    def _summary_confirm_reply(self, state, turn_id):
        state.phase = Phase.CLOSED
        state.closed_reason = "confirmed_by_user"
        state.active_question = None
        reply = AgentReply(
            "你已确认本次摘要，会谈现已结束。可以回看记录；情况变化时请重新评估。",
            Phase.CLOSED,
            kind="closed",
            summary=self.engine.replies._summary_payload(
                state, review_status="confirmed_by_user"
            ),
        )
        self.engine.replies._schedule_follow_up(state, reply, turn_id)
        return reply

    def _early_summary_confirm(self, state, user_input, ctx):
        """Summary confirmation must not be hijacked by a self-check step.

        Called from _submit before the pipeline: once the patient
        confirms the summary (button or unambiguous free text, D-03),
        the session enters the conclusion flow instead of being
        re-suspended by a pending self-check conversation. Pending
        observation/calculation feedback is a real blocker and still
        falls through.
        """
        if state.phase != Phase.SUMMARY:
            return None
        if user_input.choice_value != "confirm_summary" and not (
            is_summary_confirmation_text(ctx.text)
        ):
            return None
        if state.observation_feedback or state.calculation_feedback:
            return None
        ctx.reply_reason = "用户核对摘要"
        return self._summary_confirm_reply(state, ctx.turn_id)

    def _h_summary_review(self, state, user_input, ctx):
        if state.phase != Phase.SUMMARY:
            return None
        if user_input.choice_value == "confirm_summary" or (
            is_summary_confirmation_text(ctx.text)
        ):
            reply = self._summary_confirm_reply(state, ctx.turn_id)
        else:
            reply = self.engine.replies._finish(state)
            reply.message = "请点击摘要或右侧记录中的“更正”，修改对应信息后再核对。"
        ctx.reply_reason = "用户核对摘要"
        return reply

    def _calculation_unavailable_reply(self, state, question, text, turn_id):
        """Say plainly that this build cannot compute, then stay on the question.

        The text is kept as an event rather than a fact: what the patient said
        still has to be readable later, but it is not an answer to the pending
        question and must not satisfy its gate.
        """
        self.engine.events.append(
            Event(
                state.session_id,
                "calculation.unavailable",
                {"question_id": question.id, "evidence": text},
                turn_id=turn_id,
            )
        )
        return AgentReply(
            "这个版本还不能替你算这道数（BMI、换算这类公式要接计算能力才行）。"
            "你可以把数值直接写出来，我按你给的数值判断。"
            f"回到刚才的问题：{question.prompt}",
            question.phase,
            kind="clarification",
            question_id=question.id,
            quick_replies=question.options,
            developer_trace={
                "reason": "确定性疾病引擎不含计算插件，计算请求不作为当前问题的答案",
                "content_version": self.engine.content.versions["questions"],
            },
        )

    def _answer_active_question(self, state, question, user_input, ctx, turn_id):
        """Take the patient's words as an answer to the question on screen.

        Three ways it can go, in order: a request to compute something (the
        engine cannot, and saying so beats booking it as an answer), a refusal
        (recorded as a declined fact, so the gate is satisfied without inventing
        a value), and an ordinary answer, which is interpreted and recorded.
        """
        text = ctx.text
        if _asks_for_a_calculation(text):
            # 使用体验评测 R3-06（2026-11-05）：患者说"帮我算一下 BMI，
            # 身高175，体重70"时，规则引擎既算不出来，也不该把这句当成
            # 主诉/答案记下——那样患者等一个数值，等到的却是下一个问题。
            ctx.reply_reason = "计算请求：本引擎无计算能力，先说明再回到原问题"
            return self._calculation_unavailable_reply(state, question, text, turn_id)
        if is_refusal_text(text):
            # J-01 (deterministic engine): a refusal lands as a declined
            # fact — recorded, gate-satisfying, value-free — instead of
            # looping the question (age) or booking the refusal wording as the
            # field's value (choice).
            self.engine.subjects._record_fact(
                state,
                question.fact_key,
                Interpretation(None, Assertion.UNKNOWN, Source.PATIENT),
                turn_id,
                text,
            )
            self.engine.events.append(
                Event(
                    state.session_id,
                    "fact.declined",
                    {"fact_key": question.fact_key, "evidence": text},
                    turn_id=turn_id,
                )
            )
            state.active_question = None
            state.active_question_is_clarification = False
            return None
        result = self.engine.interpreter.interpret(question, text, user_input.choice_value)
        if question.input == "age" and (
            not isinstance(result.value, (int, float)) or not 0 <= result.value <= 120
        ):
            ctx.reply_reason = "年龄输入未通过校验"
            return AgentReply(
                "我没能确认年龄。请写成“8个月”或“32岁”这样的格式。",
                question.phase,
                kind="clarification",
                question_id=question.id,
                developer_trace={
                    "reason": "年龄格式或范围未通过确定性校验，原输入未写入年龄字段"
                },
            )
        self.engine.subjects._record_fact(state, question.fact_key, result, turn_id, text)
        state.active_question = None
        state.active_question_is_clarification = False
        return None

    def _h_intake(self, state, user_input, ctx):
        """Read the patient's answer to the question or task in front of them.

        A failed task result keeps the task on screen: the patient is told what
        to redo, and nothing is written.

        A refusal is recorded as a declined fact -- present=false, no value --
        rather than looping the question or booking the refusal wording as the
        field's value.  "I would rather not say" is an answer, and it satisfies
        the gate without inventing a clinical fact.
        """
        text, turn_id = ctx.text, ctx.turn_id
        if state.active_task:
            failure = self.engine.planning._try_task_result(
                state, text, user_input.choice_value, turn_id
            )
            if failure:
                ctx.reply_reason = "图示反馈失败，保持当前任务"
                return failure
        elif state.active_question:
            question = self.engine.content.question(state.active_question)
            return self._answer_active_question(state, question, user_input, ctx, turn_id)
        else:
            self.engine.subjects._record_fact(
                state,
                "unsolicited_note",
                Interpretation(text, Assertion.PRESENT, Source.PATIENT),
                turn_id,
                text,
            )
        return None

    def _h_risk_escalation(self, state, user_input, ctx):
        """Re-triage the turn and escalate if the risk demands it.

        The assessment is recorded as an event before it is acted on, so the
        risk level behind a reply can be read back later.

        An emergency signal that is only a bare co-occurrence is clarified
        first; a confirmed one goes straight to the emergency reply with its
        clarification attached.  Urgent risk defers to the gate, which decides
        between helping now and asking again.
        """
        text, turn_id = ctx.text, ctx.turn_id
        state.risk = self.engine.triage.assess(state, text)
        self.engine.events.append(
            Event(
                state.session_id,
                "triage.assessed",
                state.risk.to_dict(),
                turn_id=turn_id,
            )
        )
        if state.risk.level == RiskLevel.EMERGENCY:
            action = self.engine.emergency._emergency_clarification_action(state, text, ())
            if action == "clarify":
                ctx.reply_reason = "急症信号仅为裸共现，先鉴别澄清"
                return self.engine.emergency._emergency_clarification_reply(state, text, turn_id)
            if action == "confirmed":
                ctx.reply_reason = "鉴别澄清已确认，走急症路径"
                return self.engine.emergency._emergency_reply(
                    state, state.risk.triggers, include_clarification=True
                )
            ctx.reply_reason = "结构化急症门触发"
            return self.engine.emergency._emergency_reply(state, state.risk.triggers)
        if state.risk.level == RiskLevel.URGENT:
            deferred = self.engine.planning._urgent_or_defer(state, turn_id)
            if deferred is not None:
                ctx.reply_reason = "风险升级后优先求助"
                return deferred
            return None
        return None

    def _h_planning(self, state, user_input, ctx):
        reply = self.engine.planner.plan(state)
        ctx.reply_reason = (
            reply.developer_trace.get("reason", "") if reply.developer_trace else ""
        )
        return reply
