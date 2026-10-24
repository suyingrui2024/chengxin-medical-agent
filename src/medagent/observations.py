"""Host-owned repeat observations; an unsuccessful attempt never erases a reading."""
from copy import deepcopy
import re
from uuid import uuid4

from .assertion_evidence import has_uncertainty
from .domain import AgentReply, Assertion, Phase, QuickReply, RiskLevel, utc_now
from .events import Event
from .task_intents import task_intent
from .measurement_reports import temperature_report


def repeat_intent(task, text):
    # A quoted example or hypothetical reading is not a request to perform it.
    if re.search(r"如果|假如|假设|例如|比如|是否|怎么|怎样|[？?]", text):
        return None
    kind, evidence = task_intent(
        text,
        task.get("repeat_phrases", []) + task.get("request_phrases", []),
        task.get("cancel_phrases", []),
    )
    return evidence if kind == "request" else None


class ObservationMixin:
    def _repeat_task(self, state, identity):
        task = next(
            (
                t
                for t in self.content.applicable_tasks(
                    str(state.get("chief_complaint", ""))
                )
                if t["id"] == identity and t.get("repeatable") is True
            ),
            None,
        )
        if (
            task is None
            or task["result_key"] not in state.facts
            or state.active_task
            or state.observation_resume
            or self._self_check_block_reason(state)
        ):
            raise ValueError("当前任务不能重复，请先处理当前步骤或求助提示")
        return task

    def _check_observation_action(self, state, user_input):
        choice = user_input.choice_value or ""
        if not choice.startswith(("repeat_self_check:", "confirm_observation:")):
            return
        from .consultation import StaleActionError

        if user_input.expected_action_id != f"{state.session_id}:{state.revision}":
            raise StaleActionError("测量页面已经更新，请使用当前记录或图示卡片")
        if choice.startswith("repeat_self_check:"):
            try:
                self._repeat_task(state, choice.split(":", 1)[1])
            except ValueError as exc:
                raise StaleActionError(str(exc)) from exc
        else:
            resume = state.observation_resume or {}
            candidate = resume.get("candidate")
            if (
                not candidate
                or choice != f"confirm_observation:{resume.get('attempt_id')}"
            ):
                raise StaleActionError("这次读数已处理或没有待核对的读数")

    def _request_repeat(self, state, identity, text, turn_id):
        task = self._repeat_task(state, identity)
        previous = state.facts[task["result_key"]]
        old = self._latest_replies.get(state.session_id)
        attempt = {
            "id": str(uuid4()),
            "task_id": identity,
            "previous_fact_id": previous.fact_id,
            "started_at": utc_now(),
            "status": "awaiting_input",
            "source": "caregiver"
            if state.get("consult_for") in {"child", "other_adult"}
            else "patient",
            "quality": "not_done",
        }
        resume = {
            "attempt_id": attempt["id"],
            "task_id": identity,
            "phase": state.phase.value,
            "question_id": state.active_question,
            "clarification": state.active_question_is_clarification,
            "reply": old.to_dict() if old else None,
            "pending_calculation": deepcopy(state.pending_calculation),
            "calculation_resume": deepcopy(state.calculation_resume),
        }
        # A candidate is still a patient report. No model-selected number is used.
        if task["input_kind"] == "temperature":
            report = temperature_report(text)
            value = self.interpreter.temperature(report.reading_text)
            numeric_clause = report.evidence
            if (
                value is not None
                and re.search(r"腋温|腋下", numeric_clause)
                and not has_uncertainty(numeric_clause)
                and not report.uncertain
                and not re.search(r"如果|假如|假设|例如|比如|[？?]", text)
            ):
                resume["candidate"] = {"value": value, "text": text, "turn_id": turn_id}
        state.observation_resume = resume
        state.pending_self_checks = [
            item for item in state.pending_self_checks if item["task_id"] != identity
        ]
        state.observation_attempts.append(attempt)
        state.pending_calculation = state.calculation_resume = None
        state.pending_input = None
        state.active_question = None
        state.active_question_is_clarification = False
        state.active_task = identity
        state.phase = Phase.OBSERVATION
        self.events.append(
            Event(
                state.session_id,
                "self_check.repeat_started",
                deepcopy(attempt),
                turn_id=turn_id,
            )
        )
        return self._repeat_reply(state)

    def _repeat_reply(self, state, feedback=""):
        resume = state.observation_resume
        task = deepcopy(self.content.tasks[resume["task_id"]])
        previous = state.facts[task["result_key"]]
        task["repeat"] = {
            "attempt_id": resume["attempt_id"],
            "previous": previous.to_dict(),
        }
        if resume.get("candidate"):
            candidate = resume["candidate"]
            task["repeat"]["candidate"] = {
                "value": candidate["value"],
                "evidence": candidate["text"],
                "choice": f"confirm_observation:{resume['attempt_id']}",
            }
        return AgentReply(
            feedback or "再测一次；上次记录会保留。完成或跳过后继续原来的问诊。",
            Phase.OBSERVATION,
            kind="self_check",
            task=task,
            input_error=bool(feedback),
            quick_replies=[QuickReply(**item) for item in task["options"]],
        )

    def _clear_observation(self, state, reason, turn_id=None):
        resume = state.observation_resume
        if not resume:
            return
        attempt = next(
            a for a in state.observation_attempts if a["id"] == resume["attempt_id"]
        )
        attempt.update(status="cancelled", finished_at=utc_now(), reason=reason)
        self.events.append(
            Event(
                state.session_id,
                "self_check.repeat_cancelled",
                deepcopy(attempt),
                turn_id=turn_id,
            )
        )
        draft = resume.get("pending_calculation")
        if draft:
            self.events.append(
                Event(
                    state.session_id,
                    "capability.input_cancelled",
                    {
                        "proposal_id": draft["proposal_id"],
                        "reason": reason,
                    },
                    turn_id=turn_id,
                )
            )
        state.observation_resume = None
        if state.self_check_conversation and state.self_check_conversation["task"].get(
            "repeat"
        ):
            self._clear_task_conversation(state, "repeat_" + reason, turn_id)

    def _handle_observation_action(self, state, user_input, text, turn_id):
        choice = user_input.choice_value or ""
        if state.observation_resume and state.active_task:
            resume = state.observation_resume
            task = self.content.tasks[resume["task_id"]]
            from .task_conversation import is_observation_entry

            if (
                hasattr(self, "backend")
                and not choice.startswith("confirm_observation:")
                and task_intent(text, [], task.get("cancel_phrases", []))[0] != "cancel"
                and not is_observation_entry(task, text, choice)
            ):
                self._suspend_task_conversation(state, turn_id)
                return None
            source_turn = turn_id
            if choice.startswith("confirm_observation:"):
                candidate = resume["candidate"]
                text, source_turn, choice = (
                    candidate["text"],
                    candidate["turn_id"],
                    None,
                )
                self.events.append(
                    Event(
                        state.session_id,
                        "self_check.reading_confirmed",
                        {
                            "attempt_id": resume["attempt_id"],
                            "source_turn_id": source_turn,
                        },
                        turn_id=turn_id,
                    )
                )
            elif task_intent(text, [], task.get("cancel_phrases", []))[0] == "cancel":
                choice = "skip"
            failure = self._try_task_result(state, text, choice, source_turn)
            if failure:
                # A rejected entry also dismisses the earlier candidate. It must
                # not be offered as if it described the patient's edited input.
                resume.pop("candidate", None)
                return self._repeat_reply(state, failure.message)
            state.observation_resume = None
            state.risk = self.triage.assess(state, text)
            self.events.append(
                Event(
                    state.session_id,
                    "triage.assessed",
                    state.risk.to_dict(),
                    turn_id=turn_id,
                )
            )
            if state.risk.level == RiskLevel.EMERGENCY:
                return self._emergency_reply(state, state.risk.triggers)
            if state.risk.level == RiskLevel.URGENT:
                # Reintroduce only to record cancellation of an interrupted form.
                state.pending_calculation = resume.get("pending_calculation")
                return self._urgent(state)
            state.phase = Phase(resume["phase"])
            state.pending_calculation = resume.get("pending_calculation")
            state.calculation_resume = resume.get("calculation_resume")
            state.active_question = resume.get("question_id")
            state.active_question_is_clarification = resume.get("clarification", False)
            old = resume.get("reply") or {}
            if state.pending_calculation:
                reply = self._calculation_input_reply(state)
            elif old.get("calculation_result") and state.calculation_resume:
                reply = AgentReply(
                    old["message"],
                    state.phase,
                    kind="calculation_result",
                    calculation_result=old["calculation_result"],
                )
            elif state.phase == Phase.SUMMARY:
                reply = self._finish(state)
            elif (
                state.active_question
                and old.get("question_id") == state.active_question
            ):
                reply = AgentReply(
                    old["message"],
                    state.phase,
                    kind=old["kind"],
                    question_id=state.active_question,
                    quick_replies=[
                        QuickReply(**q) for q in old.get("quick_replies", [])
                    ],
                    adaptive_question=deepcopy(old.get("adaptive_question")),
                    # The restored reply IS the earlier reply: keep its
                    # developer_trace (including the bridging marker) so
                    # _record_reply does not prefix it a second time.
                    developer_trace=deepcopy(old.get("developer_trace")),
                )
            else:
                reply = self._plan_next(state)
            if not reply.summary:
                reply.observation_result = self._observation_series(state, task)
            return reply
        if choice.startswith("repeat_self_check:"):
            return self._request_repeat(state, choice.split(":", 1)[1], text, turn_id)
        for task in self.content.applicable_tasks(
            str(state.get("chief_complaint", ""))
        ):
            if (
                task.get("repeatable")
                and task["result_key"] in state.facts
                and state.facts[task["result_key"]].assertion == Assertion.PRESENT
                and not state.active_task
            ):
                if repeat_intent(task, text) and not self._self_check_block_reason(
                    state
                ):
                    report = temperature_report(text)
                    candidate = self.interpreter.temperature(report.reading_text)
                    if (
                        report.has_supplement
                        and (
                            candidate is None
                            or report.uncertain
                            or not re.search(r"腋温|腋下", report.evidence)
                        )
                        and hasattr(self, "backend")
                    ):
                        if not any(
                            item["task_id"] == task["id"]
                            for item in state.pending_self_checks
                        ):
                            request = {
                                "task_id": task["id"],
                                "evidence": text,
                                "turn_id": turn_id,
                                "repeat": True,
                            }
                            state.pending_self_checks.append(request)
                            self.events.append(
                                Event(
                                    state.session_id,
                                    "self_check.requested",
                                    {**request, "origin": "repeat_after_supplement"},
                                    turn_id=turn_id,
                                )
                            )
                        return None
                    return self._request_repeat(state, task["id"], text, turn_id)
        return None

    def _project_observation_feedback(self, state, reply):
        feedback = state.observation_feedback
        if not feedback:
            return
        # A handled location/pure reading is decision context, not a pending
        # temperature supplement or a claim that another measurement is needed.
        if (
            not feedback.get("has_supplement", True)
            or self.content.tasks[feedback["task_id"]]["input_kind"] != "temperature"
        ):
            if reply.kind != "service_error":
                state.observation_feedback = None
            return
        status = (
            "pending"
            if reply.kind == "service_error"
            else "forwarded"
            if feedback["forwarded"]
            else "unprocessed"
        )
        supplement = {"text": feedback["text"], "status": status}
        task = self.content.tasks[feedback["task_id"]]
        if reply.summary and reply.kind != "emergency":
            for series in reply.summary.get("observations", []):
                if series["task_id"] == feedback["task_id"]:
                    series["supplement"] = supplement
        elif reply.kind != "emergency":
            series = self._observation_series(state, task)
            if series:
                series["supplement"] = supplement
                if status == "pending":
                    series["can_repeat"] = False
                reply.observation_result = series
        if status != "pending":
            state.observation_feedback = None

    def _observation_completion_context(self, state):
        feedback = state.observation_feedback or {}
        if not feedback.get("completion"):
            return None
        result = deepcopy(feedback["completion"])
        current = state.facts.get(result["result_key"])
        result["result_is_current"] = bool(
            current and current.fact_id == result["fact_id"]
        )
        return result

    def _observation_series(self, state, task):
        current = state.facts.get(task["result_key"])
        if current is None:
            return None
        by_id = {f.fact_id: f for f in state.history if f.key == task["result_key"]}
        chain, seen, cursor = [], set(), current
        while cursor and cursor.fact_id not in seen:
            seen.add(cursor.fact_id)
            chain.append(cursor)
            cursor = by_id.get(cursor.supersedes)
        entries = [
            {
                "id": f.fact_id,
                "status": "recorded"
                if f.assertion == Assertion.PRESENT
                else "unconfirmed",
                "value": f.value if f.assertion == Assertion.PRESENT else None,
                "recorded_at": f.recorded_at,
                "source": f.source.value,
                "quality": f.quality.value,
                "evidence": f.evidence or "",
                "current": f.fact_id == current.fact_id,
            }
            for f in chain
        ]
        entries += [
            {
                "id": a["id"],
                "status": a["status"],
                "value": None,
                "recorded_at": a["finished_at"],
                "evidence": a.get("evidence", ""),
                "source": a.get("source", "patient"),
                "quality": a.get("quality", "not_done"),
                "current": False,
            }
            for a in state.observation_attempts
            if a["task_id"] == task["id"]
            and a["previous_fact_id"] in seen
            and a.get("finished_at")
            and a["status"] in {"skipped", "unconfirmed", "cancelled"}
        ]
        entries.sort(key=lambda e: (e["recorded_at"], e["id"]))
        visible = entries[-8:]
        if not any(e["current"] for e in visible):
            visible = [next(e for e in entries if e["current"])] + entries[-7:]
        return {
            "task_id": task["id"],
            "key": task["result_key"],
            "title": "腋温记录",
            "unit": "℃",
            "entries": visible,
            "total": len(entries),
            "can_repeat": not self._self_check_block_reason(state)
            and not state.active_task,
            "latest_status": entries[-1]["status"],
        }
