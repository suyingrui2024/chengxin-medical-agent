"""Separate conversation from observation entry; preserve the host task occurrence."""
from copy import deepcopy
import re
from uuid import uuid4

from .domain import AgentReply, Phase, QuickReply
from .events import Event
from .measurement_reports import temperature_report
from .location_revisions import LocationRevisionMixin
from .task_interactions import task_with_interaction
from .assertion_evidence import has_uncertainty, UNCERTAIN_TERMS
from .location_answers import (
    answer_spec,
    normalize_answers,
    project_answer,
    validate_draft_evidence,
    SCHEMA,
)


def location_source(text, evidence):
    """Retain the enclosing clause and adjacent location qualifiers.

    This bounded source check does not verify anatomical semantics.
    """
    matches = list(re.finditer(re.escape(evidence), text))
    if len(matches) != 1:
        raise ValueError("部位引文需能唯一定位原话，请引用更完整的连续描述。")
    match = matches[0]
    clauses = list(re.finditer(r"[^，。；,;\n]+", text))
    indices = [
        i
        for i, clause in enumerate(clauses)
        if clause.start() < match.end() and clause.end() > match.start()
    ]
    if not indices:
        raise ValueError("请引用实际的部位描述，不能只引用分隔符。")
    first, last = indices[0], indices[-1]
    for index in [first - 1, last + 1]:
        if not 0 <= index < len(clauses):
            continue
        clause = clauses[index].group()
        if not has_uncertainty(clause):
            continue
        rest = clause
        for term in sorted(UNCERTAIN_TERMS, key=len, reverse=True):
            rest = rest.replace(term, "")
        rest = re.sub(r"但是|不过|只是|具体|这个|这次|其实|现在|有点|我|也|还|但|的|了|啊|呀|呢", "", rest)
        if re.search(r"部位|位置|哪里|哪儿|疼的地方|痛的地方", clause) or not rest.strip():
            first, last = min(first, index), max(last, index)
    return text[clauses[first].start() : clauses[last].end()].strip()


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


class TaskConversationMixin(LocationRevisionMixin):
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
            "result_key": task["result_key"],
            "title": task["title"],
            "interaction_id": task["interaction_id"],
            "options": task["options"],
            "candidate_pending": bool(frame.get("candidate")),
            "status": "active" if state.active_task == task["id"] else "suspended",
            "clarification": frame.get("clarification"),
            "revision": {"previous_fact_id": frame["previous_fact_id"]}
            if frame.get("revision_resume")
            else None,
            "answer_request": (
                {
                    key: deepcopy(frame["answer_request"][key])
                    for key in ("id", "fields", "prefill")
                    if key in frame["answer_request"]
                }
                if frame.get("answer_request")
                else None
            ),
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
        if frame.get("clarification"):
            task["clarification"] = frame["clarification"]
        if frame.get("answer_request"):
            task["answer_spec"] = deepcopy(frame["answer_request"])
        if frame.get("revision_resume"):
            task["location_revision"] = {
                "previous_fact_id": frame["previous_fact_id"],
                "cancel_choice": f"cancel_location_revision:{frame['id']}",
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
            quick_replies=[
                QuickReply("取消修改，保留原记录", task["location_revision"]["cancel_choice"])
            ]
            if frame.get("revision_resume")
            else [
                QuickReply(**item)
                for item in task["options"]
                if not task.get("answer_spec") or item["value"] == "skip"
            ],
        )

    def _clarify_location_task(self, state, message, fields=None):
        spec = answer_spec(self.content.tasks["body_location"], fields)
        reply = self._resume_task_conversation(state)
        frame = state.self_check_conversation
        if frame.get("revision_resume"):
            requested = set(spec["fields"])
            if requested == {"region"}:
                requested = {"region", "area", "side", "extent"}
            else:
                requested.discard("region")
            baseline = deepcopy(frame["revision_baseline"])
            baseline["answers"] = {
                k: v for k, v in baseline["answers"].items() if k not in requested
            }
            baseline["id"] = str(uuid4())
            spec["fields"] = list(frame["answer_request"]["fields"])
            spec["prefill"] = baseline
        frame.pop("candidate", None)
        frame["clarification"] = message
        reply.task.pop("result_proposal", None)
        reply.task["clarification"] = message
        if len(spec["fields"]) > 1:
            frame["answer_request"] = spec
            reply.task["answer_spec"] = deepcopy(spec)
            reply.quick_replies = (
                reply.quick_replies
                if frame.get("revision_resume")
                else [
                    QuickReply(**item)
                    for item in frame["task"]["options"]
                    if item["value"] == "skip"
                ]
            )
        else:
            frame.pop("answer_request", None)
            reply.task.pop("answer_spec", None)
        reply.message = message
        return reply

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
            or not evidence.strip()
            or not 1 <= len(evidence) <= 1000
            or evidence not in text
        ):
            raise ValueError(
                "部位提议须使用原任务选项，并逐字引用本轮明确的部位描述；不确定时先澄清或由患者跳过，不能当作其他部位阳性。尚未记录观察结果。"
            )
        evidence = location_source(text, evidence)
        if len(evidence) > 1000:
            raise ValueError("定位原话范围过长，请先澄清，不截去限定。")
        if "input" in args and not isinstance(args["input"], dict):
            raise ValueError("定位草稿input必须是对象。")
        spec = deepcopy(frame.get("answer_request"))
        if not frame.get("answer_request") and args.get("input"):
            if not isinstance(args["input"], dict) or set(args["input"]) - {
                "area",
                "side",
                "extent",
            }:
                raise ValueError("定位草稿input只能包含area、side、extent。")
            spec = answer_spec(
                self.content.tasks["body_location"], ["region", *args["input"]]
            )
        if spec:
            raw = args.get("input", {})
            if not isinstance(raw, dict) or "region" in raw:
                raise ValueError("定位草稿input只填写area、side、extent；region使用option。")
            answers = {"region": args["option"], **deepcopy(raw)}
            if set(raw) - set(spec["fields"]):
                spec["fields"] = [
                    x
                    for x in ("region", "area", "side", "extent")
                    if x in spec["fields"] or x in raw
                ]
            normalize_answers(spec, answers, complete=False)
            grounded = validate_draft_evidence(
                spec,
                raw,
                evidence,
                args.get("input_evidence", {}),
                lambda quote: location_source(text, quote),
                args["option"],
            )
            revision = frame.get("revision_baseline")
            if revision:
                unchanged = (
                    deepcopy(revision["answers"])
                    if revision["answers"].get("region") == args["option"]
                    else {}
                )
                answers = {**unchanged, **answers}
            frame["answer_request"] = spec
            spec["prefill"] = {
                "id": str(uuid4()),
                "answers": answers,
                "evidence": evidence,
                "source_turn_id": turn_id,
                "input_evidence": grounded,
            }
            if revision:
                spec["prefill"].update(
                    origin="revision_proposal",
                    previous_fact_id=frame["previous_fact_id"],
                    baseline={
                        k: deepcopy(revision[k])
                        for k in ["answers", "evidence", "source_turn_id"]
                    },
                    note=revision.get("note", ""),
                    changed_fields=[
                        x
                        for x in answers
                        if x in raw
                        or x == "region"
                        and answers[x] != revision["answers"].get(x)
                    ],
                )
            frame.pop("candidate", None)
            self.events.append(
                Event(
                    state.session_id,
                    "self_check.answer_drafted",
                    {
                        "task_id": "body_location",
                        "form_id": spec["id"],
                        **deepcopy(spec["prefill"]),
                    },
                    turn_id=turn_id,
                )
            )
            return self._resume_task_conversation(state)
        if args.get("input") or args.get("input_evidence"):
            raise ValueError("当前没有细化定位输入卡，请先通过clarify声明answer_fields。")
        if has_uncertainty(evidence) or len(evidence) > 1000:
            raise ValueError("原话的部位范围仍有不确定限定，请先澄清或继续原图由患者选择／跳过；不能截短引文删除限定。")
        # Eligibility validation precedes proposal mutation.
        reply = self._resume_task_conversation(state)
        frame.pop("clarification", None)
        reply.task.pop("clarification", None)
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
            feedback = state.observation_feedback
            feedback["text"] = candidate["text"]
            rest = candidate["text"].replace(candidate["evidence"], "", 1)
            feedback["has_supplement"] = bool(rest.strip(" ，。；,;！!\t\r\n"))
            return candidate
        return None

    def _prepare_location_answer(self, state, user_input):
        from .consultation import StaleActionError

        frame = state.self_check_conversation
        spec = frame.get("answer_request") if frame else None
        choice = user_input.choice_value
        if choice != "confirm_location_answer":
            if user_input.observation is not None:
                raise StaleActionError("定位答案只能提交到当前核对入口。")
            if spec and choice in {x["value"] for x in spec["regions"]}:
                raise StaleActionError("当前定位还需要补充维度，请使用当前输入卡；未只记录大部位。")
            return None
        payload = user_input.observation
        if (
            not spec
            or state.active_task != "body_location"
            or user_input.expected_action_id != f"{state.session_id}:{state.revision}"
            or user_input.calculation is not None
            or not isinstance(payload, dict)
            or set(payload) != {"schema", "form_id", "answers", "note"}
            or payload["schema"] != SCHEMA
            or payload["form_id"] != spec["id"]
        ):
            raise StaleActionError("定位输入卡已更新，请核对当前卡片；未记录旧答案。")
        if frame.get("revision_resume") and (
            not state.facts.get("symptom_location")
            or state.facts["symptom_location"].fact_id != frame["previous_fact_id"]
        ):
            raise StaleActionError("原定位已变化，请从最新记录重新修改。")
        if self._self_check_block_reason(state):
            raise StaleActionError("当前应先处理风险或必要信息，定位输入尚未写入。")
        text, structured = project_answer(spec, payload["answers"], payload["note"])
        return {"text": text, "structured_input": structured}

    @staticmethod
    def _location_input_text(user_input):
        text = user_input.text
        payload = user_input.observation
        if isinstance(payload, dict):
            note = payload.get("note")
            if isinstance(note, str):
                text += "；" + note[:200]
            answers = payload.get("answers")
            area = answers.get("area") if isinstance(answers, dict) else None
            if (
                isinstance(area, dict)
                and area.get("kind") == "text"
                and isinstance(area.get("value"), str)
            ):
                text += "；" + area["value"][:120]
        return text

    def _record_location_answer(self, state, prepared, turn_id):
        from .self_checks import TaskResult
        from .interpreter import Interpretation
        from .domain import Assertion, Source, ObservationQuality

        decoded = TaskResult(
            "symptom_location",
            Interpretation(
                prepared["text"],
                Assertion.PRESENT,
                Source.PATIENT,
                ObservationQuality.NOT_ASSESSED,
            ),
        )
        self._accept_task_result(
            state,
            "body_location",
            prepared["text"],
            None,
            turn_id,
            decoded=decoded,
            structured_input=prepared["structured_input"],
        )
        state.observation_feedback["has_supplement"] = bool(
            prepared["structured_input"]["note"]
        )
        self.events.append(
            Event(
                state.session_id,
                "self_check.answer_confirmed",
                {
                    "fact_id": state.facts["symptom_location"].fact_id,
                    "form_id": prepared["structured_input"]["form_id"],
                    "answers": deepcopy(prepared["structured_input"]["answers"]),
                },
                turn_id=turn_id,
            )
        )
