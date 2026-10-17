"""Separate conversation from observation entry; preserve the host task occurrence."""
from copy import deepcopy
import re
from uuid import uuid4

from .domain import AgentReply, Phase, QuickReply
from .events import Event
from .measurement_reports import temperature_report
from .task_interactions import task_with_interaction
from .assertion_evidence import has_uncertainty


def is_observation_entry(task, text, choice):
    """Only deterministic controls or measurement-shaped input use the parser.

    This is a routing boundary, not proof of a measurement or semantic diagnosis.
    Natural-language locations go to the planner and require patient confirmation.
    """
    if choice in {item["value"] for item in task["options"]}:
        return True
    if task["input_kind"] != "temperature":
        return False
    report = temperature_report(text)
    return bool(
        re.search(r"腋温|腋下|体温|温度|℃|摄氏|华氏|℉|°\s*[CcFf]", text)
        # Malformed numeric entries still receive a local input error. Full
        # narratives such as age or medication reports instead reach the model.
        or re.fullmatch(
            r"\s*[-+\d][\d.eE,+°℃℉%/\s\-和还或者是到FfCcKkGgMmLl]*[。！!]?\s*", text
        )
        or text.strip(" 。！!") in {"完成了", "测好了", "量好了", "已完成"}
        or report.uncertain
        or not report.has_supplement
    )


class TaskConversationMixin:
    def _clear_task_conversation(self, state, reason, turn_id=None):
        frame = state.self_check_conversation
        if frame:
            self.events.append(
                Event(
                    state.session_id,
                    "self_check.conversation_cleared",
                    {
                        "task_id": frame["task"]["id"],
                        "interaction_id": frame["task"]["interaction_id"],
                        "reason": reason,
                    },
                    turn_id=turn_id,
                )
            )
            state.self_check_conversation = None

    def _suspend_task_conversation(self, state, turn_id):
        identity = state.active_task
        frame = state.self_check_conversation
        if not frame:
            latest = self._latest_replies.get(state.session_id)
            task = (
                deepcopy(latest.task)
                if latest and latest.task and latest.task["id"] == identity
                else None
            )
            if task is None:
                task = task_with_interaction(
                    self.content.tasks[identity],
                    state.session_id,
                    f"{state.session_id}:{state.revision}",
                    self.events.for_session(state.session_id),
                )
            previous = state.facts.get(task["result_key"])
            frame = {
                "id": str(uuid4()),
                "task": task,
                "previous_fact_id": previous.fact_id if previous else None,
            }
            state.self_check_conversation = frame
        # New text supersedes any unconfirmed model suggestion, never a fact.
        frame.pop("candidate", None)
        frame["task"].pop("result_proposal", None)
        state.active_task = None
        state.active_question = None
        state.active_question_is_clarification = False
        state.phase = Phase.HISTORY
        self.events.append(
            Event(
                state.session_id,
                "self_check.conversation_suspended",
                {
                    "task_id": identity,
                    "interaction_id": frame["task"]["interaction_id"],
                },
                turn_id=turn_id,
            )
        )

    def _task_conversation_context(self, state):
        frame = state.self_check_conversation
        if not frame:
            return None
        task = frame["task"]
        return {
            "task_id": task["id"],
            "title": task["title"],
            "interaction_id": task["interaction_id"],
            "options": task["options"],
            "candidate_pending": bool(frame.get("candidate")),
            "status": "active" if state.active_task == task["id"] else "suspended",
        }

    def _resume_task_conversation(self, state):
        frame = state.self_check_conversation
        if not frame:
            raise ValueError("没有可接续的原图示任务。")
        task = deepcopy(frame["task"])
        eligible = {
            t["id"]
            for t in self.content.applicable_tasks(
                str(state.get("chief_complaint", ""))
            )
        }
        previous = state.facts.get(task["result_key"])
        if (
            task["id"] not in eligible
            or (previous.fact_id if previous else None) != frame["previous_fact_id"]
            or (task.get("repeat") and not state.observation_resume)
        ):
            self._clear_task_conversation(state, "task_no_longer_current")
            raise ValueError("原任务依据已经变化，请按最新病例重新选择。")
        blocked = self._self_check_block_reason(state)
        if blocked:
            raise ValueError(blocked)
        if frame.get("candidate"):
            candidate = frame["candidate"]
            task["result_proposal"] = {
                "option": candidate["option"],
                "evidence": candidate["evidence"],
                "choice": f"confirm_self_check:{candidate['id']}",
            }
        state.active_task = task["id"]
        state.active_question = None
        state.active_question_is_clarification = False
        state.phase = Phase.OBSERVATION
        self.events.append(
            Event(
                state.session_id,
                "self_check.conversation_resumed",
                {"task_id": task["id"], "interaction_id": task["interaction_id"]},
            )
        )
        return AgentReply(
            "继续这一步；可以补充其他信息，也可以跳过。",
            state.phase,
            kind="self_check",
            task=task,
            quick_replies=[QuickReply(**item) for item in task["options"]],
        )

    def _propose_task_result(self, state, args, text, turn_id):
        frame = state.self_check_conversation
        if (
            not frame
            or frame["task"]["id"] != args["task_id"]
            or args["task_id"] != "body_location"
        ):
            raise ValueError("只能为当前未完成的部位定位提议选项；不能提议体温或新查体。")
        options = {item["value"] for item in frame["task"]["options"]} - {
            "skip",
            "unable",
            "not_available",
        }
        evidence = args["evidence"]
        if (
            args["option"] not in options
            or not isinstance(evidence, str)
            or not 1 <= len(evidence) <= 1000
            or evidence not in text
            or has_uncertainty(evidence)
        ):
            raise ValueError(
                "部位提议须使用原任务选项，并逐字引用本轮明确的部位描述；不确定时先澄清或由患者跳过，不能当作其他部位阳性。尚未记录观察结果。"
            )
        # Eligibility validation precedes proposal mutation.
        reply = self._resume_task_conversation(state)
        frame["candidate"] = {
            "id": str(uuid4()),
            "option": args["option"],
            "evidence": evidence,
            "text": text,
            "source_turn_id": turn_id,
        }
        self.events.append(
            Event(
                state.session_id,
                "self_check.result_proposed",
                {"task_id": args["task_id"], **deepcopy(frame["candidate"])},
                turn_id=turn_id,
            )
        )
        reply.task["result_proposal"] = {
            "option": args["option"],
            "evidence": evidence,
            "choice": f"confirm_self_check:{frame['candidate']['id']}",
        }
        return reply

    def _check_task_conversation_action(self, state, user_input):
        choice = user_input.choice_value or ""
        if not choice.startswith(("resume_self_check:", "confirm_self_check:")):
            return
        # Import lazily: consultation owns the public exception and mixes us in.
        from .consultation import StaleActionError

        frame = state.self_check_conversation
        expected = f"{state.session_id}:{state.revision}"
        valid = bool(frame and user_input.expected_action_id == expected)
        if choice.startswith("resume_self_check:"):
            latest = self._latest_replies.get(state.session_id)
            valid = (
                valid
                and choice == f"resume_self_check:{frame['id']}"
                and bool(
                    latest and any(q.value == choice for q in latest.quick_replies)
                )
            )
        else:
            candidate = frame.get("candidate") if frame else None
            valid = valid and bool(
                candidate
                and state.active_task == frame["task"]["id"]
                and choice == f"confirm_self_check:{candidate['id']}"
            )
        if not valid:
            raise StaleActionError("图示核对入口已更新，请使用当前页面；未重复记录。")

    def _handle_task_conversation_action(self, state, user_input, turn_id):
        choice = user_input.choice_value or ""
        if choice.startswith("resume_self_check:"):
            return self._resume_task_conversation(state)
        if choice.startswith("confirm_self_check:"):
            frame = state.self_check_conversation
            candidate = deepcopy(frame["candidate"])
            self.events.append(
                Event(
                    state.session_id,
                    "self_check.result_confirmed",
                    {
                        "task_id": frame["task"]["id"],
                        "proposal_id": candidate["id"],
                        "source_turn_id": candidate["source_turn_id"],
                    },
                    turn_id=turn_id,
                )
            )
            self._accept_task_result(
                state,
                frame["task"]["id"],
                candidate["evidence"],
                candidate["option"],
                candidate["source_turn_id"],
            )
            return candidate
        return None
