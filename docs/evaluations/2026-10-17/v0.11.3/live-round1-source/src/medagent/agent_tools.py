"""Medical capability definitions and atomic, evidence-grounded fact proposals."""
from __future__ import annotations

from typing import Any, Dict, List

from .domain import Assertion, CaseState
from .knowledge import ContentRepository
from .interpreter import Interpreter
from .assertion_evidence import absence_evidence_error, has_uncertainty


TOOL_DEFINITIONS = [
    {
        "name": "record_facts",
        "description": "从本轮原文提议事实。原子校验整批；每个事实必须附本轮原文的连续引文。",
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
        "name": "ask_question",
        "description": "结束当前回合，问一个候选问题。",
        "arguments": {"question_id": "candidate ID"},
    },
    {
        "name": "clarify",
        "description": "就一个已配置字段灵活澄清。仅提一个简短问题，不给诊断、剂量或自行查体动作。",
        "arguments": {"topic": "known field", "message": "one question"},
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
        "name": "show_self_check",
        "description": "调用满足适用条件的固定图示任务。",
        "arguments": {"task_id": "registered task"},
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
]


class ProposalError(ValueError):
    pass


ARGUMENTS = {
    "record_facts": {"facts"},
    "inspect_case": set(),
    "list_questions": set(),
    "ask_question": {"question_id"},
    "clarify": {"topic", "message"},
    "show_self_check": {"task_id"},
    "request_self_check": {"task_id", "evidence"},
    "review_summary": set(),
    "run_capability": {"id", "input", "evidence"},
}


def validate_command(command: Dict[str, Any]) -> None:
    if set(command) != {"tool", "arguments"} or not isinstance(
        command["arguments"], dict
    ):
        raise ProposalError("工具提议必须仅含 tool 和 arguments")
    name, args = command["tool"], command["arguments"]
    if name not in ARGUMENTS:
        raise ProposalError("工具未注册，不能执行：" + str(name))
    if set(args) != ARGUMENTS[name]:
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
