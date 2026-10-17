"""Version-bound location revisions retain the old fact until explicit confirmation."""
from copy import deepcopy
import re
from uuid import uuid4

from .domain import Phase, RiskLevel
from .events import Event
from .location_answers import answer_spec
from .task_interactions import task_with_interaction
from .task_intents import task_intent


class LocationRevisionMixin:
    def _location_revision_target(self, state, fact_id, current_input=None):
        fact = state.facts.get("symptom_location")
        task = self.content.tasks.get("body_location")
        if (
            not fact
            or fact.fact_id != fact_id
            or not task
            or not task.get("answer_contract")
            or not any(
                t["id"] == "body_location"
                for t in self.content.applicable_tasks(
                    str(state.get("chief_complaint", ""))
                )
            )
            or state.risk.level in {RiskLevel.EMERGENCY, RiskLevel.URGENT}
            or state.assertion("emergency_screen").value != "absent"
            or state.phase == Phase.CLOSED
            and state.closed_reason != "confirmed_by_user"
            or state.active_task
            or state.self_check_conversation
            or state.observation_resume
            or state.pending_calculation
            or state.calculation_resume
            or state.calculation_feedback
            or state.pending_input
            and state.pending_input != current_input
            or state.observation_feedback
            or state.subject_resume
        ):
            raise ValueError("定位记录或当前步骤已变化，请先处理当前图示、计算、重试或求助入口。")
        return task, fact

    def _location_revision_entry(self, state):
        fact = state.facts.get("symptom_location")
        if not fact:
            return None
        try:
            self._location_revision_target(state, fact.fact_id)
        except ValueError:
            return None
        return {"fact_id": fact.fact_id, "choice": f"revise_location:{fact.fact_id}"}

    def _check_location_revision_action(self, state, user_input):
        from .consultation import StaleActionError

        choice = user_input.choice_value or ""
        frame = state.self_check_conversation or {}
        editing = frame.get("revision_resume")
        opening = choice.startswith("revise_location:")
        cancelling = (
            choice.startswith("cancel_location_revision:")
            or editing
            and choice == "skip"
        )
        if editing and choice == "confirm_summary":
            raise StaleActionError("请先确认或取消定位修改，再核对摘要。")
        if not opening and not cancelling:
            return
        if (
            user_input.expected_action_id != f"{state.session_id}:{state.revision}"
            or user_input.calculation is not None
            or user_input.observation is not None
        ):
            raise StaleActionError("定位修改入口已更新，请使用当前记录。")
        if opening:
            try:
                self._location_revision_target(state, choice.split(":", 1)[1])
            except ValueError as exc:
                raise StaleActionError(str(exc)) from exc
        elif not editing or choice not in {
            "skip",
            f"cancel_location_revision:{frame['id']}",
        }:
            raise StaleActionError("这次定位修改已结束，未改变原记录。")

    @staticmethod
    def _location_record_answers(task, fact):
        structured = fact.structured_input or {}
        answers = {}
        regions = {x["value"] for x in task["options"] if x["value"] != "skip"}
        for field, item in structured.get("answers", {}).items():
            if field == "area":
                if item.get("reported_text"):
                    answers[field] = {"kind": "text", "value": item["reported_text"]}
                elif item["assertion"] == "unknown":
                    answers[field] = {"kind": "unknown", "value": None}
                else:
                    preset = any(
                        x["value"] == item["value"]
                        for values in task["answer_contract"]["area_presets"].values()
                        for x in values
                    )
                    answers[field] = {
                        "kind": "preset" if preset else "text",
                        "value": item["value"],
                    }
            elif field in {"region", "side", "extent"}:
                answers[field] = (
                    "unknown" if item["assertion"] == "unknown" else item["value"]
                )
        if answers.get("region") not in regions:
            answers.pop("region", None)
        if not answers and fact.assertion.value == "present":
            option = next(
                (
                    x
                    for x in task["options"]
                    if x["label"] == fact.value and x["value"] in regions
                ),
                None,
            )
            if option:
                answers["region"] = option["value"]
        return answers

    def _start_location_revision(self, state, fact_id, turn_id, current_input=None):
        task, fact = self._location_revision_target(state, fact_id, current_input)
        spec = answer_spec(task, ["region", "area", "side", "extent"])
        spec["prefill"] = {
            "id": str(uuid4()),
            "origin": "record",
            "previous_fact_id": fact.fact_id,
            "answers": self._location_record_answers(task, fact),
            "evidence": fact.evidence or str(fact.value or "上次未确认位置"),
            "source_turn_id": fact.turn_id,
            "note": (fact.structured_input or {}).get("note", ""),
        }
        old = deepcopy(self._latest_replies.get(state.session_id))
        state.self_check_conversation = {
            "id": str(uuid4()),
            "task": task_with_interaction(
                task,
                state.session_id,
                f"{state.session_id}:{state.revision}",
                self.events.for_session(state.session_id),
            ),
            "previous_fact_id": fact.fact_id,
            "answer_request": spec,
            "revision_baseline": deepcopy(spec["prefill"]),
            "clarification": "修改需要更正的项，再核对记录。",
            "revision_resume": {
                "reply": old,
                "phase": state.phase,
                "closed_reason": state.closed_reason,
                "question_id": state.active_question,
                "clarification": state.active_question_is_clarification,
                "fact_ids": {k: f.fact_id for k, f in state.facts.items()},
                "previous_note": (fact.structured_input or {}).get("note", ""),
            },
        }
        if state.phase == Phase.CLOSED:
            state.phase, state.closed_reason = Phase.SUMMARY, None
        self.events.append(
            Event(
                state.session_id,
                "self_check.location_revision_started",
                {"fact_id": fact.fact_id, "form_id": spec["id"]},
                turn_id=turn_id,
            )
        )
        return self._resume_task_conversation(state)

    def _request_location_revision(self, state, args, text, turn_id):
        evidence = args.get("evidence")
        if (
            args.get("task_id") != "body_location"
            or not isinstance(evidence, str)
            or not 2 <= len(evidence) <= 1000
            or evidence not in text
            or not re.search(
                r"更正|修改|改成|改为|改一下|改下|说错|选错|标错|记错|填错|写错|有误|不对|不是|重新核对", evidence
            )
            or not re.search(r"位置|部位|定位|左|右|额头|太阳穴|后脑|头顶|胸|腹|背|肩|腿|手|脚", evidence)
            or re.search(r"如果|假如|假设|例如|比如|不想|不要|不需要|不用|取消", evidence)
        ):
            raise ValueError("仅根据患者明确更正定位的原话建立核对卡，不直接改记录。")
        fact = state.facts.get("symptom_location")
        if not fact:
            raise ValueError("当前没有可修改的定位记录。")
        return self._start_location_revision(state, fact.fact_id, turn_id, text)

    def _restore_location_revision_host(self, state, resume, changed):
        old = deepcopy(resume["reply"])
        state.active_task = None
        if resume["phase"] == Phase.CLOSED and not changed:
            state.phase, state.closed_reason = Phase.CLOSED, resume["closed_reason"]
            state.active_question = None
            return old
        state.closed_reason = None
        if resume["phase"] in {Phase.SUMMARY, Phase.CLOSED}:
            return self._finish(state)
        question = next(
            (q for q in self.content.questions if q.id == resume["question_id"]), None
        )
        if (
            question
            and old
            and old.question_id == question.id
            and self.content.question_applies(
                question, str(state.get("chief_complaint", ""))
            )
        ):
            current = state.facts.get(question.fact_key)
            if (current.fact_id if current else None) == resume["fact_ids"].get(
                question.fact_key
            ):
                state.phase = resume["phase"]
                state.active_question = question.id
                state.active_question_is_clarification = resume["clarification"]
                old.developer_trace = {
                    **(old.developer_trace or {}),
                    "resume_question_action_id": old.action_id,
                }
                return old
        state.active_question = None
        state.active_question_is_clarification = False
        return self._plan_next(state)

    def _handle_location_revision_action(self, state, user_input, text, turn_id):
        choice = user_input.choice_value or ""
        if choice.startswith("revise_location:"):
            return self._start_location_revision(
                state, choice.split(":", 1)[1], turn_id
            )
        frame = state.self_check_conversation or {}
        if not frame.get("revision_resume"):
            return None
        cancel = choice.startswith("cancel_location_revision:") or choice == "skip"
        cancel = (
            cancel
            or task_intent(text, [], frame["task"].get("cancel_phrases", []))[0]
            == "cancel"
        )
        if cancel:
            resume = deepcopy(frame["revision_resume"])
            self._clear_task_conversation(state, "revision_cancelled", turn_id)
            return self._restore_location_revision_host(state, resume, False)
        return None

    def _complete_location_revision(self, state, prepared, turn_id):
        frame = state.self_check_conversation or {}
        if not frame.get("revision_resume"):
            return None
        resume, previous_id = (
            deepcopy(frame["revision_resume"]),
            frame["previous_fact_id"],
        )
        prepared["structured_input"]["revision_of"] = previous_id
        self._record_location_answer(state, prepared, turn_id)
        state.observation_feedback["has_supplement"] = bool(
            prepared["structured_input"]["note"]
            and prepared["structured_input"]["note"] != resume["previous_note"]
        )
        self.events.append(
            Event(
                state.session_id,
                "self_check.location_revision_completed",
                {
                    "previous_fact_id": previous_id,
                    "fact_id": state.facts["symptom_location"].fact_id,
                },
                turn_id=turn_id,
            )
        )
        return self._restore_location_revision_host(state, resume, True)

    @staticmethod
    def _remember_location_revision_question(state, reply):
        frame = state.self_check_conversation or {}
        resume = frame.get("revision_resume")
        if resume and reply.kind in {"question", "clarification"} and reply.question_id:
            resume.update(
                reply=deepcopy(reply),
                phase=state.phase,
                question_id=state.active_question,
                clarification=state.active_question_is_clarification,
                fact_ids={k: f.fact_id for k, f in state.facts.items()},
            )
