"""Medical capability definitions and atomic, evidence-grounded fact proposals."""
from __future__ import annotations

from typing import Any, Dict, List

from .domain import Assertion, CaseState
from .knowledge import ContentRepository
from .interpreter import Interpreter
from .assertion_evidence import absence_evidence_error, has_absence_cue, has_uncertainty


TOOL_DEFINITIONS = [
    {
        "name": "record_facts",
        "description": "从本轮原文提议fact_schema中的问诊事实。原子校验整批；每个事实必须附本轮原文的连续引文。体温、部位等observation_fields_read_only由宿主图示处理，不得通过此工具写入。",
        "arguments": {
            "facts": [
                {
                    "key": "known field",
                    "value": "typed value or null",
                    "assertion": "present|absent|unknown",
                    "evidence": "exact quote",
                    "correction": False,
                }
            ]
        },
    },
    {"name": "inspect_case", "description": "查看已知、未知、冲突和缺口，不修改状态。", "arguments": {}},
    {"name": "list_questions", "description": "获取当前适用且未处理的固定问题候选。", "arguments": {}},
    {
        "name": "change_subject",
        "description": "用户转为咨询另一位家人时展示独立问诊入口，不修改当前病例。引用明确的新对象原话；普通家族史不属于换人。",
        "arguments": {"evidence": "exact new subject quote"},
    },
    {
        "name": "ask_question",
        "description": "结束当前回合，问一个候选问题。",
        "arguments": {"question_id": "candidate ID"},
    },
    {
        "name": "ask_patient",
        "description": (
            "针对一个尚未处理的注册字段生成一个患者问题和0到8个上下文相关选项。"
            "按钮文字与数量可以动态变化；文字模式不带选项。单选可标记需要补充，"
            "多选可标记互斥项。allow_other启用自由补充。风险标签只能描述选项中"
            "明确可见的症状，不是诊断或隐藏推断。"
        ),
        "arguments": {
            "topic": "registered fact field",
            "question": "one concise patient question",
            "selection_mode": "single|multiple|text",
            "options": [
                {
                    "id": "stable snake_case id",
                    "label": "patient-visible button text",
                    "response_text": "patient answer submitted when selected; must include label verbatim",
                    "risk_tags": [],
                    "exclusive": False,
                    "requires_detail": False,
                    "detail_placeholder": "optional prompt when detail is required",
                }
            ],
            "allow_other": True,
            "other_label": "其他情况",
            "other_placeholder": "请补充具体情况",
        },
    },
    {
        "name": "clarify",
        "description": "就问诊字段或当前symptom_location澄清。定位时用answer_fields声明需回答的region/area/side/extent，宿主展示对应输入；粗部位只选region。定位补问最多80字，不写伴随症状。其他主题不传answer_fields。",
        "arguments": {
            "topic": "question field or active symptom_location",
            "message": "one question",
            "answer_fields": ["region", "area", "side", "extent"],
        },
    },
    {
        "name": "request_self_check",
        "description": "把用户明确提出的图示请求登记为待办。附本轮连续引文；登记不执行任务，前置条件满足后由控制器处理。",
        "arguments": {
            "task_id": "requestable task",
            "evidence": "exact user request quote",
        },
    },
    {
        "name": "repeat_self_check",
        "description": "用户明确请求重新测量时，重复已注册且允许重复的图示任务；旧读数保留，失败或跳过不清除旧值。附本轮连续引文。",
        "arguments": {
            "task_id": "repeatable task",
            "evidence": "exact user request quote",
        },
    },
    {
        "name": "propose_self_check_result",
        "description": "把自然语言部位描述提议为待核对草稿，不写病例。仅当前body_location。可选input填写原话已给的area/side/extent，宿主按需创建细化输入卡；area为{kind:preset|text|unknown,value:代码或原话或null}。代码见answer_contract。缺信息留空，未提及不能填unknown。input_evidence可按维度给完整定位引文中的连续原话，区分明确位置与不确定范围；不删去同句限定。先记录同轮其他病史。",
        "arguments": {
            "task_id": "body_location",
            "option": "registered location option",
            "evidence": "exact location quote",
            "input": {
                "side": "right",
                "area": {"kind": "preset", "value": "temple"},
                "extent": "area",
            },
        },
    },
    {
        "name": "show_self_check",
        "description": "调用满足适用条件的固定图示任务。",
        "arguments": {"task_id": "registered task"},
    },
    {
        "name": "revise_self_check",
        "description": "患者明确更正已记录的不适位置时建立修改卡，不直接写事实。仅body_location，需引用明确更正原话；先处理当前其他图示、计算或重试。调用后观察返回的self_check_conversation，可用propose_self_check_result整理本轮明确的修改，再让患者核对；未修改的维度沿用原记录并明确来源。",
        "arguments": {
            "task_id": "body_location",
            "evidence": "exact correction request quote",
        },
    },
    {
        "name": "review_summary",
        "description": "展示可核对的事实摘要，询问是否准确。需先处理必要缺口。",
        "arguments": {},
    },
    {
        "name": "run_capability",
        "description": "患者明确请求时提出注册换算，展示可编辑输入卡；缺参数保持空白。必须引用本轮请求原文，数值和原单位不准补造。核对卡不会立即执行计算。",
        "arguments": {
            "id": "enabled capability ID",
            "input": {},
            "evidence": "exact user request quote",
        },
    },
    {
        "name": "spawn_subagent",
        "description": "把一个边界明确的子任务交给受控子智能体并行处理（证据检索或盲化二意见）。子智能体只读：只能查看 scope 声明的病例投影，不能写事实、不能与患者对话、不能再派生；其产出经宿主证据逐字校验后才以数据形式返回，未通过校验的内容会被丢弃。task_kind 只能是 evidence_lookup 或 second_opinion；每轮至多派生一次。",
        "arguments": {
            "task_kind": "evidence_lookup",
            "objective": "...",
            "branches": [
                {
                    "branch_id": "b1",
                    "objective": "...",
                    "scope": {
                        "fact_keys": [],
                        "hypotheses": [],
                        "transcript": "none",
                        "prior_case": False,
                    },
                }
            ],
            "tools": [],
            "budget": {},
        },
    },
]


class ProposalError(ValueError):
    pass


ARGUMENTS = {
    "record_facts": {"facts"},
    "inspect_case": set(),
    "list_questions": set(),
    "change_subject": {"evidence"},
    "ask_question": {"question_id"},
    "ask_patient": {
        "topic",
        "question",
        "selection_mode",
        "options",
        "allow_other",
    },
    "clarify": {"topic", "message"},
    "show_self_check": {"task_id"},
    "revise_self_check": {"task_id", "evidence"},
    "propose_self_check_result": {"task_id", "option", "evidence"},
    "repeat_self_check": {"task_id", "evidence"},
    "request_self_check": {"task_id", "evidence"},
    "review_summary": set(),
    "run_capability": {"id", "input", "evidence"},
    "spawn_subagent": {"task_kind", "objective", "branches"},
}


def validate_command(command: Dict[str, Any]) -> None:
    if set(command) != {"tool", "arguments"} or not isinstance(
        command["arguments"], dict
    ):
        raise ProposalError("工具提议必须仅含 tool 和 arguments")
    name, args = command["tool"], command["arguments"]
    if name not in ARGUMENTS:
        raise ProposalError("工具未注册，不能执行：" + str(name))
    optional = (
        {"answer_fields"}
        if name == "clarify"
        else {"other_label", "other_placeholder"}
        if name == "ask_patient"
        else {"input", "input_evidence"}
        if name == "propose_self_check_result"
        else {"tools", "budget"}
        if name == "spawn_subagent"
        else set()
    )
    if not ARGUMENTS[name] <= set(args) or set(args) - ARGUMENTS[name] - optional:
        raise ProposalError("工具参数未满足注册定义")


def validate_proposals(
    content: ContentRepository, state: CaseState, raw_text: str, proposals: Any
) -> List[Dict[str, Any]]:
    if not isinstance(proposals, list) or not 1 <= len(proposals) <= 20:
        raise ProposalError("facts 必须是 1 到 20 项的数组")
    fields = {q.fact_key: q for q in content.questions}
    output = []
    seen = set()
    for item in proposals:
        if not isinstance(item, dict) or set(item) - {
            "key",
            "value",
            "assertion",
            "evidence",
            "correction",
        }:
            raise ProposalError("事实包含未允许的属性")
        key, value = item.get("key"), item.get("value")
        if not isinstance(key, str) or key not in fields or key in seen:
            raise ProposalError("字段必须已注册且在本批次内唯一")
        seen.add(key)
        evidence = item.get("evidence")
        if (
            not isinstance(evidence, str)
            or not evidence.strip()
            or evidence not in raw_text
        ):
            raise ProposalError("evidence 必须是本轮输入的连续原文，不能虚构引文")
        try:
            assertion = Assertion(item.get("assertion"))
        except (ValueError, TypeError) as exc:
            raise ProposalError("assertion 无效") from exc
        if assertion == Assertion.UNKNOWN and not has_uncertainty(evidence):
            raise ProposalError("未知必须引用患者明确表示不知道或无法确认的原文；未提及的字段应保持缺失并继续询问")
        if assertion == Assertion.ABSENT:
            error = absence_evidence_error(raw_text, evidence)
            if error:
                raise ProposalError(error)
        if fields[key].input == "yes_no_unknown":
            uncertain = has_uncertainty(evidence)
            negative = any(phrase in evidence for phrase in ("没有", "没", "无", "否认", "不"))
            if uncertain and assertion != Assertion.UNKNOWN:
                raise ProposalError("不确定的筛查不能写成阳性或阴性，请使用 unknown/null")
            if assertion == Assertion.PRESENT and negative:
                raise ProposalError("筛查引文含否定，不能作为阳性确认；请核对否定范围")
            if assertion == Assertion.ABSENT:
                collective = evidence.strip(" 。！!，,") in {"没有", "无", "否认", "全无"} or any(
                    phrase in evidence
                    for phrase in (
                        "都没有",
                        "均没有",
                        "均无",
                        "全部没有",
                        "没有上述",
                        "没有这些",
                        "没有危险信号",
                        "没有紧急",
                        "无上述",
                        "否认上述",
                        "否认这些",
                        "没有以上",
                    )
                )
                if not collective:
                    raise ProposalError("否认单项或其他事情不能视为全部急症筛查阴性，请澄清或保留未知")
        if key in state.facts and item.get("correction") is not True:
            raise ProposalError("覆盖已有事实必须显式声明 correction=true")
        if assertion != Assertion.PRESENT:
            if value is not None:
                raise ProposalError("unknown/absent 的 value 必须为 null")
            if assertion == Assertion.ABSENT and fields[key].input not in {
                "text_or_absent",
                "yes_no_unknown",
            }:
                raise ProposalError("该字段不支持阴性；无法确认时使用 unknown")
        else:
            question = fields[key]
            if question.input == "age":
                if type(value) not in (int, float) or not 0 <= value <= 120:
                    raise ProposalError("年龄必须是 0 到 120 的岁数值")
                parsed = Interpreter().interpret(question, evidence).value
                if type(parsed) not in (int, float) or abs(parsed - value) > 0.002:
                    raise ProposalError("年龄数值与原文不一致")
            elif question.input == "severity" and type(value) in (int, float):
                if not 0 <= value <= 10:
                    raise ProposalError("评分必须在 0 到 10")
            elif question.input == "yes_no_unknown":
                if value is not True:
                    raise ProposalError("阳性急症筛查只能为 true；阴性用 absent/null")
            elif question.input == "choice":
                if not isinstance(value, str) or value not in {
                    o.value for o in question.options if o.value != "unknown"
                }:
                    raise ProposalError("枚举字段值未注册")
            elif not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ProposalError("文本字段必须是非空短文本")
            if question.input == "text_or_absent":
                if has_uncertainty(evidence) and not has_uncertainty(value):
                    raise ProposalError(
                        "文本叙述必须保留引文中的不确定限定，或使用 unknown/null；不能规范化为已确认结论。"
                    )
                if has_absence_cue(value) and not has_uncertainty(value):
                    error = absence_evidence_error(raw_text, evidence)
                    if error:
                        raise ProposalError("不能借 present 文本写入未确认的否认：" + error)
        output.append(
            {
                "key": key,
                "value": value,
                "assertion": assertion,
                "evidence": evidence,
                "correction": item.get("correction") is True,
            }
        )
    return output
