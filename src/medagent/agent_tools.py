"""Medical capability definitions and atomic, evidence-grounded fact proposals."""
from __future__ import annotations

from typing import Any, Dict, List

from .domain import Assertion, CaseState
from .knowledge import ContentRepository
from .interpreter import Interpreter
from .assertion_evidence import (
    absence_error_guidance,
    absence_evidence_error,
    has_absence_cue,
    has_uncertainty,
)


TOOL_DEFINITIONS = [
    {
        "name": "record_facts",
        "description": "从患者原话提议fact_schema中的问诊事实。逐条校验：通过项先写入，未通过项不写入并逐条返回原因与修复指引；只需修正未通过项后单独重提，已写入项不要重复提议（再次提议须correction=true）。每个事实必须附患者原话的连续逐字引文——本轮输入；更正既有事实时也可引用该事实原始记录所在轮次的原话。澄清否认当轮除外：患者否认危险信号澄清后，可引用被澄清的首轮主诉原话为尚未落账的主诉事实作证。体温、部位等observation_fields_read_only由宿主图示处理，不得通过此工具写入。",
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
        "description": "展示可核对的事实摘要，询问是否准确。需先处理必要缺口；主诉触发的鉴别通路若尚未探明（其细节字段未问/未记且无鉴别证据落账，见 context 的 pathway_exploration），调用会被拒绝：先探明对应细节字段，或确不适用时填写 abstain_reason 说明理由后重提。",
        "arguments": {"abstain_reason": "optional string; only to close while a triggered pathway is unexplored"},
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
    """Validation failure whose message is fed back to the model verbatim.

    ``verdicts`` optionally carries the structured per-item results of a
    record_facts batch ({"key", "ok", plus "reason"/"guidance"/"evidence"
    for failures) for callers that want more than the message text.
    ``accepted`` carries the normalized proposals that passed validation,
    so a caller implementing partial recording can land exactly those
    without re-validating (and without ever landing a rejected item).
    """

    def __init__(
        self, message: str, verdicts: Any = None, accepted: Any = None
    ) -> None:
        super().__init__(message)
        self.verdicts = verdicts
        self.accepted = accepted


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
        raise ProposalError(
            "工具未注册，不能执行：" + str(name) + "；请只使用工具目录中注册的工具名"
        )
    optional = (
        {"abstain_reason"}
        if name == "review_summary"
        else {"answer_fields"}
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
        missing = sorted(ARGUMENTS[name] - set(args))
        extra = sorted(set(args) - ARGUMENTS[name] - optional)
        detail = []
        if missing:
            detail.append("缺少参数：" + "、".join(missing))
        if extra:
            detail.append("未注册的参数：" + "、".join(extra))
        raise ProposalError(
            "工具参数未满足注册定义（" + "；".join(detail) + "），请按注册定义补齐后重提"
        )


class _ItemRejection(Exception):
    """One proposal item's failure: a reason plus repair guidance."""

    def __init__(self, reason: str, guidance: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.guidance = guidance


def _evidence_sources(
    state: CaseState, raw_text: str, key: str, correction: bool
) -> List[str]:
    """Texts a quote may verbatim come from, current turn first.

    Verbatim-ness is never relaxed. The only widening is turn scope, and only
    for a correction: besides the current input, the patient turn that
    originally produced the fact being corrected (located via the recorded
    evidence in the transcript, falling back to the recorded evidence
    itself). Quotes from other turns stay invalid, so a superseded fact
    cannot be resurrected by re-quoting its stale source turn.

    One narrow exception (P5, C-07/C-08): on the turn where the patient
    denies an emergency clarification, the clarified first turn's text is
    also a source — but only for facts not yet recorded, and only when
    the current input is exactly that denial turn's input (the adaptive
    engine stamps the marker with for_input). The denial negates the
    danger signals, not the chief-complaint statement itself, so quoting
    the chief statement in the denial round is not a stale resurrection;
    on any other turn the marker is inert and the current-turn rule
    applies unchanged.
    """
    sources = [raw_text]
    if not correction and key not in state.facts:
        snapshot = getattr(state, "runtime_snapshot", None) or {}
        denied = snapshot.get("emergency_clarification_denied")
        if (
            isinstance(denied, dict)
            and denied.get("for_input") == raw_text
            and isinstance(denied.get("text"), str)
            and denied["text"].strip()
            and denied["text"] not in sources
        ):
            sources.append(denied["text"])
    if correction:
        existing = state.facts.get(key)
        if existing is not None and existing.evidence:
            for entry in state.transcript or []:
                if (
                    isinstance(entry, dict)
                    and entry.get("role") == "user"
                    and isinstance(entry.get("content"), str)
                    and existing.evidence in entry["content"]
                    and entry["content"] not in sources
                ):
                    sources.append(entry["content"])
            if existing.evidence not in sources:
                sources.append(existing.evidence)
    return sources


def _validate_item(
    fields: Dict[str, Any],
    state: CaseState,
    raw_text: str,
    item: Any,
    seen: set,
) -> Dict[str, Any]:
    """Validate one proposal item; raise _ItemRejection on its first failure."""
    if not isinstance(item, dict):
        raise _ItemRejection(
            "事实项必须是包含 key/value/assertion/evidence 的对象",
            "修复：把每一项写成 {key, value, assertion, evidence} 对象，不要用字符串或数组代替。",
        )
    extra = set(item) - {"key", "value", "assertion", "evidence", "correction"}
    if extra:
        raise _ItemRejection(
            "事实包含未允许的属性：" + "、".join(sorted(extra)),
            "修复：事实项只允许 key、value、assertion、evidence、correction 五个属性，请删掉多余属性后重提。",
        )
    key, value = item.get("key"), item.get("value")
    if not isinstance(key, str) or key not in fields:
        raise _ItemRejection(
            "字段必须已注册：" + (key if isinstance(key, str) else repr(key)),
            "修复：key 只能使用 fact_schema 中已注册的字段名，不要自创字段；不确定时先 inspect_case 查看已注册字段。",
        )
    if key in seen:
        raise _ItemRejection(
            "字段在本批次内重复提议",
            "修复：同一批次内同一字段只能出现一次，请把该字段的信息合并成一项再提。",
        )
    seen.add(key)
    correction = item.get("correction") is True
    evidence = item.get("evidence")
    source = None
    if isinstance(evidence, str) and evidence.strip():
        for candidate in _evidence_sources(state, raw_text, key, correction):
            if evidence in candidate:
                source = candidate
                break
    if source is None:
        raise _ItemRejection(
            "evidence 必须是本轮输入的连续原文，不能虚构引文",
            "修复：从患者原话中逐字复制一段连续文字作为 evidence，不要改字、删字或跨句拼接；"
            "更正既有事实时也可引用该事实原始记录所在轮次的患者原话（须 correction=true），"
            "其他轮次的原话不能用作新事实的引文。澄清否认当轮除外：患者否认危险信号澄清后，"
            "可引用被澄清的首轮主诉原话为尚未落账的主诉事实作证（否认仅否定危险信号，不否定主诉陈述本身）。",
        )
    try:
        assertion = Assertion(item.get("assertion"))
    except (ValueError, TypeError) as exc:
        raise _ItemRejection(
            "assertion 无效：" + repr(item.get("assertion")),
            "修复：assertion 只能是 present、absent、unknown 之一。",
        ) from exc
    if assertion == Assertion.UNKNOWN and not has_uncertainty(evidence):
        raise _ItemRejection(
            "未知必须引用患者明确表示不知道或无法确认的原文；未提及的字段应保持缺失并继续询问",
            "修复：患者明确说不知道、记不清时，引文须包含这类原话才可记 unknown；"
            "患者根本没提到的字段不要提议该项，保持缺失并继续提问。",
        )
    if assertion == Assertion.ABSENT:
        error = absence_evidence_error(source, evidence)
        if error:
            raise _ItemRejection(error, absence_error_guidance(error))
    if fields[key].input == "yes_no_unknown":
        uncertain = has_uncertainty(evidence)
        negative = any(phrase in evidence for phrase in ("没有", "没", "无", "否认", "不"))
        if uncertain and assertion != Assertion.UNKNOWN:
            raise _ItemRejection(
                "不确定的筛查不能写成阳性或阴性，请使用 unknown/null",
                "修复：引文含不确定表达时只能记 unknown、value=null；要记阳性或阴性，先澄清得到明确回答。",
            )
        if assertion == Assertion.PRESENT and negative:
            raise _ItemRejection(
                "筛查引文含否定，不能作为阳性确认；请核对否定范围",
                "修复：请核对否定词的范围——患者否认该筛查项时按阴性证据要求记 absent，范围不清时先澄清，不要记 present。",
            )
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
                raise _ItemRejection(
                    "否认单项或其他事情不能视为全部急症筛查阴性，请澄清或保留未知",
                    "修复：整个筛查记 absent 必须引用患者对全部筛查项目的整体否认原话（如「都没有」"
                    "「没有这些」）；只否认单项时不要记整个筛查为 absent，可保留未知并逐项澄清。",
                )
    if key in state.facts and not correction:
        raise _ItemRejection(
            "覆盖已有事实必须显式声明 correction=true",
            "修复：确认要覆盖该字段时，在该项加 correction=true，并给出支持新结论的引文"
            "（可引用该事实原始记录轮次的原话）；不修改就不要重复提议该字段。",
        )
    if assertion != Assertion.PRESENT:
        if value is not None:
            raise _ItemRejection(
                "unknown/absent 的 value 必须为 null",
                "修复：absent/unknown 时把 value 改为 null；叙述内容请改记 present（并满足引文要求）或直接去掉该项。",
            )
        if assertion == Assertion.ABSENT and fields[key].input not in {
            "text_or_absent",
            "yes_no_unknown",
        }:
            raise _ItemRejection(
                "该字段不支持阴性；无法确认时使用 unknown",
                "修复：该字段只能记 present 或 unknown；无法确认时用 unknown/null，不要记 absent。",
            )
    else:
        question = fields[key]
        if question.input == "age":
            if type(value) not in (int, float) or not 0 <= value <= 120:
                raise _ItemRejection(
                    "年龄必须是 0 到 120 的岁数值",
                    "修复：value 填患者原话中的岁数数值（0–120），不要推算或换算。",
                )
            parsed = Interpreter().interpret(question, evidence).value
            if type(parsed) not in (int, float) or abs(parsed - value) > 0.002:
                raise _ItemRejection(
                    "年龄数值与原文不一致",
                    "修复：使 value 等于引文中出现的岁数，并确保引文包含该数字的患者原话。",
                )
        elif question.input == "severity" and type(value) in (int, float):
            if not 0 <= value <= 10:
                raise _ItemRejection(
                    "评分必须在 0 到 10",
                    "修复：严重度 value 必须是 0–10 之间的数值，按患者原话填写。",
                )
        elif question.input == "yes_no_unknown":
            if value is not True:
                raise _ItemRejection(
                    "阳性急症筛查只能为 true；阴性用 absent/null",
                    "修复：阳性时 value 只能为 true；阴性请改用 assertion=absent、value=null，不要用 false。",
                )
        elif question.input == "choice":
            if not isinstance(value, str) or value not in {
                o.value for o in question.options if o.value != "unknown"
            }:
                raise _ItemRejection(
                    "枚举字段值未注册",
                    "修复：value 必须是该字段的注册选项值之一，不要自创；不确定选哪项时改记 unknown 或先查看候选。",
                )
        elif not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise _ItemRejection(
                "文本字段必须是非空短文本",
                "修复：value 须是基于患者原话的非空叙述（不超过 2000 字），不要留空或用数字代替。",
            )
        if question.input == "text_or_absent":
            if has_uncertainty(evidence) and not has_uncertainty(value):
                raise _ItemRejection(
                    "文本叙述必须保留引文中的不确定限定，或使用 unknown/null；不能规范化为已确认结论。",
                    "修复：把 value 改成保留原话限定词的叙述（原样带上「好像、应该、记不清」等限定），"
                    "或改用 assertion=unknown、value=null；临床归纳时不要丢掉限定词。",
                )
            if has_absence_cue(value) and not has_uncertainty(value):
                error = absence_evidence_error(source, evidence)
                if error:
                    raise _ItemRejection(
                        "不能借 present 文本写入未确认的否认：" + error,
                        "修复：二选一——①改记 absent（value=null），引文按阴性证据要求处理："
                        + absence_error_guidance(error)
                        + "②保留 present 时，从 value 中删去引文不支持的否认结论，只写引文明确肯定的内容。",
                    )
    return {
        "key": key,
        "value": value,
        "assertion": assertion,
        "evidence": evidence,
        "correction": correction,
    }


def validate_proposals(
    content: ContentRepository, state: CaseState, raw_text: str, proposals: Any
) -> List[Dict[str, Any]]:
    if not isinstance(proposals, list) or not 1 <= len(proposals) <= 20:
        raise ProposalError("facts 必须是 1 到 20 项的数组")
    fields = {q.fact_key: q for q in content.questions}
    output = []
    verdicts: List[Dict[str, Any]] = []
    seen: set = set()
    for index, item in enumerate(proposals):
        label = (
            item["key"]
            if isinstance(item, dict) and isinstance(item.get("key"), str)
            else f"第{index + 1}项"
        )
        try:
            output.append(_validate_item(fields, state, raw_text, item, seen))
        except _ItemRejection as rejection:
            verdicts.append(
                {
                    "key": label,
                    "ok": False,
                    "reason": rejection.reason,
                    "guidance": rejection.guidance,
                    "evidence": item.get("evidence") if isinstance(item, dict) else None,
                }
            )
        else:
            verdicts.append({"key": label, "ok": True})
    failed = [verdict for verdict in verdicts if not verdict["ok"]]
    if failed:
        lines = [
            f"record_facts 整批校验未通过（共 {len(verdicts)} 项：通过 {len(verdicts) - len(failed)} 项、"
            f"未通过 {len(failed)} 项）。整批为原子校验，未通过项修正前一条都不会写入；"
            "请按下述逐条指引修正后整批重提，已通过项保持原样一并重提。逐条结果："
        ]
        for verdict in verdicts:
            if verdict["ok"]:
                lines.append(f"- 「{verdict['key']}」✓ 通过")
            else:
                line = f"- 「{verdict['key']}」✗ 未通过：{verdict['reason']}"
                if isinstance(verdict.get("evidence"), str) and verdict["evidence"]:
                    line += f"（问题引文：「{verdict['evidence'][:80]}」）"
                line += "。" + verdict["guidance"]
                lines.append(line)
        raise ProposalError("\n".join(lines), verdicts=verdicts, accepted=output)
    return output
