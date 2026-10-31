"""Mixin: calculation for ConsultationEngine."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class ConsultationEngineCalculation:
    def _calculation_input_reply(self, state, feedback=""):
        draft = deepcopy(state.pending_calculation)
        draft["feedback"] = feedback
        if draft.get("form_kind") == "scale_form/v1":
            total = len(draft.get("items") or [])
            message = f"下面有 {total} 项，请逐条选择；全部选完再提交计算。"
        elif draft.get("form_kind") == "formula_form/v1":
            count = len(draft.get("inputs") or [])
            message = f"请填写这 {count} 项数值，核对后再计算。"
        else:
            message = "核对这三个输入，再开始换算。"
        return AgentReply(
            message,
            state.phase,
            kind="calculation_input",
            calculation_input=draft,
        )

    def _check_calculation_action(self, state, user_input):
        choice = user_input.choice_value
        if choice and choice.startswith("process_calculation_supplement:"):
            if (
                user_input.expected_action_id != f"{state.session_id}:{state.revision}"
                or user_input.calculation is not None
                or not state.calculation_feedback
                or choice.split(":", 1)[1] != state.calculation_feedback["id"]
                or state.pending_calculation
                or state.calculation_resume
                or state.active_task
                or state.observation_feedback
            ):
                raise StaleActionError("补充入口已更新，请使用当前页面；原话未重复处理。")
            return
        if (
            choice == "confirm_summary"
            and state.calculation_feedback
            and not state.calculation_feedback["processing"]
        ):
            raise StaleActionError("还有同轮补充待处理，请先处理后再核对摘要。")
        if choice not in {
            "confirm_calculation",
            "skip_calculation",
            "resume_after_calculation",
        }:
            if user_input.calculation is not None:
                raise StaleActionError("计算参数需要对应当前输入卡，尚未提交")
            return
        if user_input.expected_action_id != f"{state.session_id}:{state.revision}":
            raise StaleActionError("输入卡已更新，请核对当前卡片后提交")
        payload = user_input.calculation
        if not isinstance(payload, dict):
            raise StaleActionError("未收到当前计算卡片的标识")
        if choice == "resume_after_calculation":
            if (
                set(payload) != {"execution_id"}
                or not state.calculation_resume
                or payload["execution_id"]
                != state.calculation_resume.get("execution_id")
            ):
                raise StaleActionError("这项计算已处理，请使用当前问诊页面")
        elif (
            set(payload) != {"proposal_id", "input"}
            or not state.pending_calculation
            or payload["proposal_id"] != state.pending_calculation["proposal_id"]
        ):
            raise StaleActionError("计算提议已失效，请使用当前输入卡")

    def _clear_calculation(self, state, reason, turn_id=None):
        if state.pending_calculation and state.calculation_feedback:
            state.calculation_feedback["outcome"] = "cancelled"
        if state.pending_calculation:
            self.events.append(
                Event(
                    state.session_id,
                    "capability.input_cancelled",
                    {
                        "proposal_id": state.pending_calculation["proposal_id"],
                        "reason": reason,
                    },
                    turn_id=turn_id,
                )
            )
        state.pending_calculation = None
        state.calculation_resume = None

    def _resume_calculation(self, state):
        resume = state.calculation_resume or {}
        state.pending_calculation = None
        state.calculation_resume = None
        old = resume.get("reply") or {}
        question = next(
            (
                q
                for q in self.content.questions
                if q.id == resume.get("question_id") and q.id == state.active_question
            ),
            None,
        )
        if question and (
            question.fact_key not in state.facts or resume.get("clarification")
        ):
            reply = self._ask(state, question, "计算交互结束，继续此前未回答的问题")
            if old.get("question_id") == question.id and old.get("kind") == "question":
                reply.message = old["message"]
                reply.quick_replies = [
                    QuickReply(**item) for item in old.get("quick_replies", [])
                ]
                reply.adaptive_question = deepcopy(old.get("adaptive_question"))
                # Same restore rule as the observation path: the restored
                # reply keeps its developer_trace (including the bridging
                # marker) so _record_reply does not prefix it a second time.
                reply.developer_trace = deepcopy(old.get("developer_trace"))
                state.active_question_is_clarification = bool(
                    resume.get("clarification")
                )
                if state.active_question_is_clarification:
                    state.phase = reply.phase = Phase(resume["phase"])
            return reply
        if (
            resume.get("phase") == "summary"
            and state.phase == Phase.SUMMARY
            and old.get("kind") == "summary"
        ):
            return self._finish(state)
        return None

    def _handle_calculation_action(self, state, user_input, text, turn_id):
        choice = user_input.choice_value
        if choice == "confirm_calculation":
            draft = state.pending_calculation
            try:
                values = confirmed_parameters(
                    self.content.registry, draft, user_input.calculation["input"]
                )
                result = self.run_capability(
                    state,
                    draft["capability_id"],
                    values,
                    "patient_confirmed_form",
                    turn_id,
                    confirmation={
                        "proposal_id": draft["proposal_id"],
                        "source_quote": draft["source_quote"],
                        "source_turn_id": draft["source_turn_id"],
                        "confirmation_turn_id": turn_id,
                        "original_input": deepcopy(draft["original_input"]),
                        "confirmed_input": deepcopy(values),
                    },
                )
            except (ValueError, TypeError):
                candidate = user_input.calculation.get("input")
                if isinstance(candidate, dict):
                    if state.pending_calculation.get("form_kind") == "scale_form/v1":
                        # Keep only integer answers to items that belong
                        # to this scale; everything else is dropped.
                        allowed = {
                            item["id"]
                            for item in state.pending_calculation.get("items") or []
                        }
                        for key, value in candidate.items():
                            if key in allowed and type(value) is int:
                                state.pending_calculation["input"][key] = value
                    elif (
                        state.pending_calculation.get("form_kind")
                        == "formula_form/v1"
                    ):
                        # Keep only decimal strings for this formula's
                        # own inputs; a bad value is preserved so the
                        # patient can correct it instead of retyping all.
                        allowed = {
                            item["id"]
                            for item in state.pending_calculation.get("inputs") or []
                        }
                        for key, value in candidate.items():
                            if (
                                key in allowed
                                and isinstance(value, str)
                                and len(value) <= 12
                            ):
                                state.pending_calculation["input"][key] = value
                    else:
                        for key, value in candidate.items():
                            if (
                                key == "value"
                                and isinstance(value, str)
                                and len(value) <= 25
                            ):
                                state.pending_calculation["input"][key] = value
                            elif (
                                key in {"from_unit", "to_unit"}
                                and isinstance(value, str)
                                and value
                                in {
                                    unit["value"]
                                    for unit in state.pending_calculation["unit_options"]
                                }
                            ):
                                state.pending_calculation["input"][key] = value
                return self._calculation_input_reply(
                    state,
                    state.pending_calculation.get("feedback_hint", "未完成计算，请核对参数。"),
                )
            state.pending_calculation = None
            if state.calculation_feedback:
                state.calculation_feedback["outcome"] = "completed"
            state.calculation_resume["execution_id"] = result["execution_id"]
            if (result.get("data") or {}).get("result_renderer") == "scale/v1":
                message = "量表已计分，作答已核对。结果只反映本次作答，不构成诊断。"
            elif (result.get("data") or {}).get("result_renderer") == "formula/v1":
                message = "计算已完成，输入已核对。结果只作记录与参考，不构成诊断。"
            else:
                message = "换算已完成，参数已核对。实际测量质量仍未评价。"
            return AgentReply(
                message,
                state.phase,
                kind="calculation_result",
                calculation_result=deepcopy(result),
            )
        if choice in {"skip_calculation", "resume_after_calculation"}:
            if choice == "skip_calculation":
                if state.calculation_feedback:
                    state.calculation_feedback["outcome"] = "skipped"
                self.events.append(
                    Event(
                        state.session_id,
                        "capability.input_cancelled",
                        {
                            "proposal_id": state.pending_calculation["proposal_id"],
                            "reason": "patient_skipped",
                        },
                        turn_id=turn_id,
                    )
                )
            return self._resume_calculation(state)
        if state.pending_calculation or state.calculation_resume:
            self._clear_calculation(state, "new_patient_message", turn_id)
        return None

    def _check_fact_version(self, state, key, expected_fact_id):
        if expected_fact_id is not None and (
            key not in state.facts or state.facts[key].fact_id != expected_fact_id
        ):
            raise StaleActionError("这项记录已更新，请核对最新内容后再更正；你的输入尚未提交")

    def _prepare_fact_updates(self, state, updates, turn_id):
        """Apply shared dependency rules only after the whole batch validates.

        A confirmed consultation subject is a case boundary. Age corrections
        refer to the same person and retain their reported measurements.
        """
        current_subject = subject_profile(state)
        for item in updates:
            requested = (
                proposed_profile(item["value"], item.get("evidence"))
                if item["key"] == "consult_for"
                and item["assertion"] == Assertion.PRESENT
                else None
            )
            if item["key"] == "consult_for" and "consult_for" in state.facts:
                previous = state.facts["consult_for"]
                if previous.assertion == Assertion.PRESENT and (
                    item["assertion"] != previous.assertion
                    or item["value"] != previous.value
                ):
                    raise SubjectBoundaryError(
                        "咨询对象已固定。更换咨询对象请开始新的问诊，以免混用不同人的病史", requested
                    )
                if (
                    current_subject
                    and current_subject["reference"]
                    and requested
                    and requested["reference"]
                    and not compatible(
                        current_subject["reference"], requested["reference"]
                    )
                ):
                    raise SubjectBoundaryError("这次问诊的对象不同，请为另一位家人新建问诊。", requested)
        self._apply_fact_update_dependencies(state, updates, turn_id)
