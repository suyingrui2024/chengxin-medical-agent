"""收尾层 (closing layer): closing text appended at session close.

逐字搬运自 consultation.py（基线 6458b7d），仅改落点、不改逻辑：
- 9 个 helper（设计文档 §3.2 归属清单）本体在此，consultation.py
  仅留薄转发，_record_reply 直接调用本模块的三个公开函数。
- 本模块只改 reply.message（及读取 sheet/state），不改 state.phase、
  state.conclusion、处置级别与事件；fail-open 的 try/except 原样保留。
- _close_window_text 是 _close_direct_sentence 的私有依赖，随体搬运，
  非 §3.2 清单的独立项；consultation.py 的旧件已无调用方，由监督
  复核删除（见监督报告裁定 a），此处为唯一本体。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .domain import AgentReply, CaseState

def _sheet_worry_goal_text(sheet: Dict[str, Any], include_goal: bool = True) -> str:
    """Single source of the worry/goal append rule (was inline in
    _speak_close_line): worry response first, goal response
    second, each stripped, given a terminal 。/！/？ when missing.
    A missing or malformed field degrades to no append, never to
    a broken close line. include_goal=False drops only the goal
    piece (N-11 urgent suppression); the default keeps the
    long-standing confirmed-close layout byte-identical."""
    extra = ""
    try:
        worry = sheet.get("worry") or {}
        worry_response = (
            worry.get("response") if isinstance(worry, dict) else None
        )
        goal_response = sheet.get("goal_response") if include_goal else None
        for piece in (worry_response, goal_response):
            if isinstance(piece, str) and piece.strip():
                piece = piece.strip()
                if not piece.endswith(("。", "！", "？")):
                    piece += "。"
                extra += piece
    except Exception:
        extra = ""
    return extra


def _strip_close_facts_echo(text: str) -> str:
    """Cut the urgent head's collected-facts list out of a close
    text before the goal-part addressed check. The list restates
    recorded facts in the patient's words — including the goal
    itself — so leaving it in makes every echoed part look
    answered. The cut is delimited by the list's own lead-in and
    tail; text without a facts list passes through untouched."""
    start = text.find("已采集的信息：")
    if start < 0:
        return text
    tail = "不必等问诊补齐再求助。"
    end = text.find(tail, start)
    if end < 0:
        return text
    return text[:start] + text[end + len(tail):]


def _append_sheet_worry_goal(
    reply: AgentReply, sheet: Dict[str, Any]
) -> None:
    """Shared close append (P2): the sheet's worry response and
    goal response, verbatim, in that order. Used by the urgent
    close path in _record_reply; the confirmed close path uses
    _sheet_worry_goal_text for the same rule so its byte layout
    (extra before the boilerplate) is unchanged.

    N-11/O-20/D-16/Y-17/K-18: the urgent head already gives the
    graded destination answer ("要不要马上去急诊：要"), so the
    goal_response fixed sentence ("不足以直接回答") next to it
    contradicts it. Suppress only that goal_response piece when
    the graded answer is already in the message; the worry
    response still renders, and closes without a graded answer
    (the O4 routine shape: mitigation only, no direct sentence)
    keep the goal_response untouched."""
    goal_response = sheet.get("goal_response") if isinstance(sheet, dict) else None
    graded = "要不要马上去急诊：" in reply.message
    mitigated = isinstance(goal_response, str) and "不足以直接回答" in goal_response
    reply.message += _sheet_worry_goal_text(
        sheet, include_goal=not (graded and mitigated)
    )


def _append_unaddressed_goals(
    state: CaseState,
    reply: AgentReply,
    sheet: Dict[str, Any],
    visible_text: str,
    include_exam_parts: bool = False,
) -> None:
    """P2 goal-part close append (mechanism ②): for each goal
    part with no handling in visible_text, append one sentence
    "关于‘{part}’：{handling}". Handling comes from the sheet's
    own self-care items or the fixed insufficient-information
    sentence — never newly composed clinical content. Fail-open:
    any failure leaves the close untouched. Callers pass the
    close text *without* the goal_response quote (see
    conclusion.unaddressed_goal_parts) or the check is vacuous.
    include_exam_parts (urgent closes only) additionally walks
    examination parts (O-19), which the shared walk skips as
    visit parts.

    中文契约（§6.2）：调用方必须传去掉 goal_response 引文后
    的 visible 文本，否则检查恒真失效——即 visible_text 中
    若仍含 goal_response 的引文，part 会被误判为已回应，
    应 append 的 per-part 句子会被漏掉（反例：传含引文文本
    时 part 被误判为已回应——基线行为如此，新模块亦然，
    故调用方必须先去引文）。"""
    try:
        from .conclusion import (
            _GOAL_PART_FIXED_HANDLING,
            _keyword_addressed,
            _part_keywords,
            goal_part_handling,
            unaddressed_goal_parts,
            unaddressed_urgent_goal_parts,
        )

        goal_line = sheet.get("goal_line") or ""
        chief_text = ""
        chief_fact = state.facts.get("chief_complaint")
        if chief_fact is not None:
            if chief_fact.value is not None:
                chief_text += str(chief_fact.value)
            if chief_fact.evidence:
                chief_text += str(chief_fact.evidence)
        list_parts = (
            unaddressed_urgent_goal_parts
            if include_exam_parts
            else unaddressed_goal_parts
        )
        for part in list_parts(goal_line, visible_text):
            handling = goal_part_handling(part, sheet, chief_text)
            # N-S1-1 single-source dedup: the addressed check
            # above deliberately excludes the worry/goal extra,
            # so a part whose handling is only the fixed
            # insufficient-information sentence would re-quote a
            # target the close already renders (goal_response's
            # "你问到‘…’" or the worry response's "你问过是不是
            # ‘…’") without adding information. Both call sites
            # run this after the extra is already in
            # reply.message, so a keyword hit there means the
            # target renders once already: drop the per-part
            # sentence. A self-care handling always appends: it
            # adds new information even when the target was
            # quoted, and mechanism ② keeps its pinned cases
            # (all goal_response-None visit goals, unaffected).
            # The dedup text is echo-stripped and
            # negation-aware, like the addressed check itself:
            # a facts-list echo is not a rendering, and a
            # negated mention ("不缓解") is not one either —
            # a raw substring hit on either used to swallow
            # the sentence (O-19/D-14/D-15/O-17).
            dedup_text = _strip_close_facts_echo(reply.message)
            if handling == _GOAL_PART_FIXED_HANDLING and any(
                _keyword_addressed(keyword, dedup_text)
                for keyword in _part_keywords(part)
            ):
                continue
            sentence = f"关于‘{part}’：{handling}"
            if not sentence.endswith(("。", "！", "？")):
                sentence += "。"
            reply.message += sentence
    except Exception:
        return


def _append_unconfirmed_background_note(
    state: CaseState, reply: AgentReply
) -> None:
    """④-4 note option: list background items never recorded at
    all (用药 / 过敏 / 病史) so the offline evaluation confirms
    them first. Presence in any assertion state counts as asked;
    only wholly absent keys are named. No clinical content is
    added and the urgent gate itself is untouched."""
    labels = (
        ("medications_taken", "用药"),
        ("allergies", "过敏"),
        ("relevant_history", "病史"),
    )
    missing = [label for key, label in labels if key not in state.facts]
    if not missing:
        return
    note = (
        "以下背景信息本次尚未确认，线下评估时请首先确认："
        + "、".join(missing)
        + "。"
    )
    if note not in reply.message:
        reply.message += note


def _append_uncertainty_note(
    reply: AgentReply, sheet: Dict[str, Any]
) -> None:
    """G-12 (a): render the sheet's explicit uncertainty note on
    the close text, once, when the composer set one."""
    note = sheet.get("uncertainty_note") if isinstance(sheet, dict) else None
    if isinstance(note, str) and note.strip():
        note = note.strip()
        if not note.endswith(("。", "！", "？")):
            note += "。"
        if note not in reply.message:
            reply.message += note


def append_scope_note(state: CaseState, reply: AgentReply) -> None:
    """P3 scope disclosure (read-only reuse): on an off-pathway
    close, append out_of_pathway.SCOPE_NOTE once. The pathway
    check reuses conclusion.chief_matches_pathway over the
    recorded chief value + evidence; a missing chief never
    triggers the note."""
    try:
        from .conclusion import chief_matches_pathway
        from .out_of_pathway import SCOPE_NOTE
    except Exception:
        return
    if SCOPE_NOTE in reply.message:
        return
    chief_fact = state.facts.get("chief_complaint")
    chief = ""
    if chief_fact is not None:
        if chief_fact.value is not None:
            chief += str(chief_fact.value)
        if chief_fact.evidence:
            chief += " " + str(chief_fact.evidence)
    chief = chief.strip()
    if not chief or chief_matches_pathway(chief):
        return
    reply.message += SCOPE_NOTE


def _close_window_text(disposition: Dict[str, Any], window_hours) -> Optional[str]:
    """Window wording from conclusion.py, when that worker lands it.

    The conclusion worker provides
    ``format_window_text(disposition, window_hours)`` as the single
    source of window wording; this side must not invent its own
    numeric window. On the pre-merge baseline that helper does not
    exist yet, so this returns None and callers fall back to the
    disposition-level wording ("今天内") that already matches the
    urgent_today disposition text. The call is tolerant of the
    helper taking a level string instead of a dict. This side
    never reads any field of the disposition other than level;
    only the returned window string is ever rendered.
    """
    try:
        from .conclusion import format_window_text
    except Exception:
        return None
    level = disposition.get("level") if isinstance(disposition, dict) else disposition
    stripped = {"level": level, "window_hours": window_hours}
    # Try the caller's disposition first (the signature the
    # conclusion worker was briefed on), then the stripped
    # level/window form, then a bare level string. This side
    # never reads any other field of the passed disposition;
    # only the returned window string is ever rendered.
    candidates = []
    if isinstance(disposition, dict):
        candidates.append(disposition)
    candidates.extend([stripped, level])
    for arg in candidates:
        try:
            out = format_window_text(arg, window_hours)
        except Exception:
            continue
        if isinstance(out, str) and out.strip():
            return out.strip()
    return None


def _close_direct_sentence(
    window_hours, disposition=None
) -> str:
    """Direct answer to "要不要马上去急诊" for urgent_today.

    Level wording only: go to emergency assessment today. It is
    unconditional on the patient-goal fact, so cases where the
    goal question was skipped before the urgent close (K-13/F-11
    pattern) still get the answer in the visible close.
    """
    window_text = _close_window_text(
        disposition if disposition is not None else {"level": "urgent_today"},
        window_hours,
    )
    if window_text:
        if window_text.endswith(("。", "！", "？")):
            # The helper's window sentence names the generic entry
            # ("线下医疗机构"); the direct-answer slot must name
            # the level destination itself ("急诊"), so that one
            # entry wording is specialised here. The time wording
            # ("今天内") and the rest of the sentence still come
            # from the helper; the sheet's own disposition text
            # elsewhere is untouched.
            specialised = window_text.replace("线下医疗机构", "急诊", 1)
            return f"要不要马上去急诊：要，{specialised}"
        return f"要不要马上去急诊：要，{window_text}去急诊当面评估。"
    return "要不要马上去急诊：要，今天内去急诊当面评估。"


def render_confirmed_close(
    state: CaseState, reply: AgentReply, sheet: Optional[Dict[str, Any]] = None
) -> None:
    """Say the disposition out loud on a user-confirmed close.

    The conclusion sheet renders as a card, but the closing chat
    message itself used to be pure boilerplate, so a patient reading
    only the chat stream never heard the disposition. The sheet only
    exists after _attach_conclusion runs inside _record_reply, so the
    line is attached at that choke point rather than in the close
    handlers (both engines share it). Fail-open: no attached sheet
    (sheet mode off/shadow, or a composer failure) leaves the
    boilerplate untouched.

    触发条件归属（§3.4）：transition / guard reply 防重复由
    _record_reply 把关，本函数不判断 transition；kind /
    closed_reason / sheet 非空是本函数的原有守卫，原样保留。
    """
    if sheet is None:
        sheet = reply.conclusion
    if (
        reply.kind != "closed"
        or state.closed_reason != "confirmed_by_user"
        or sheet is None
    ):
        return
    try:
        from .conclusion import spoken_close_line

        line = spoken_close_line(sheet)
    except Exception:
        return
    if line:
        # C-02 (controller side, batch A minimum): when the patient
        # asked whether they need an examination and the disposition
        # is home observation, the close line must not end with only
        # "暂时不需要跑一趟医院". Say when offline evaluation is still
        # needed and which entry takes it — from the sheet's own
        # safety net, never a new threshold. A department hint is
        # named only when the sheet marks it visible (C12 sign-off
        # owns department visibility); otherwise the generic entry
        # wording is used. Broader close-template rework, if wanted,
        # is batch B GOAL overlap, not done here.
        sentence = ""
        sheet = sheet or {}
        disposition = sheet.get("disposition") or {}
        goal_line = sheet.get("goal_line") or ""
        if disposition.get("level") == "self_care" and any(
            keyword in goal_line
            for keyword in ("检查", "去医院", "就诊", "看医生")
        ):
            entry = (
                sheet.get("department_hint")
                if sheet.get("department_visible")
                else None
            ) or "线下医疗机构"
            sentence = (
                "你问到要不要去检查：目前的建议是先在家观察；"
                "如果出现结论单安全网中列出的情况，或症状持续不缓解、"
                f"加重，仍需线下评估，届时可凭本结论单到{entry}就诊。"
            )
        # Batch B (WORRY/GOAL): after the batch-A sentence, append
        # the sheet's own worry response and goal response verbatim,
        # in that order. These are the composer's words, already
        # screened by the sheet lint; nothing is composed here, so
        # no direction label or department name can leak in through
        # this path (C3/C12 visibility stays with the sheet). The
        # composer excludes batch-A exam-keyword goals from
        # goal_response, so that goal class is still answered only
        # by the batch-A sentence above, never twice. A missing or
        # malformed worry/goal field degrades to no append, never
        # to a broken close line. The rule lives in
        # _sheet_worry_goal_text (shared with the urgent path);
        # this call site keeps the original byte layout (extra
        # before the boilerplate).
        extra = _sheet_worry_goal_text(sheet)
        # Batch C (CLOSE-COPY): a confirmed close whose sheet is
        # urgent_today also speaks the level direct answer, so the
        # chat stream alone answers "要不要马上去急诊". This is
        # inserted before the batch-A sentence and the batch-B
        # worry/goal appends, whose relative order is untouched;
        # self_care sheets (all batch-B tests) get no direct
        # sentence and are byte-identical to before. Window
        # wording inside the direct sentence comes from
        # _close_direct_sentence (format_window_text when present,
        # otherwise the urgent_today level wording); no new window
        # is invented here.
        direct = ""
        if disposition.get("level") == "urgent_today":
            direct = _close_direct_sentence(
                disposition.get("window_hours") or 24,
                disposition=disposition,
            )
        # P2 goal parts: the addressed check runs against the
        # close text *without* the worry/goal extra — the
        # goal_response quote must not count as its own answer.
        original = reply.message
        visible = line + direct + sentence + original
        reply.message = line + direct + sentence + extra + original
        _append_unaddressed_goals(state, reply, sheet, visible)
        _append_uncertainty_note(reply, sheet)


def render_urgent_close(
    state: CaseState, reply: AgentReply, sheet: Dict[str, Any]
) -> None:
    """Urgent close rendering (今日 _record_reply 内 urgent 分支的全部行为)。

    顺序原样：_append_sheet_worry_goal（含 N-11 抑制）→
    _append_unaddressed_goals（含 exam parts）→ _append_uncertainty_note
    → _append_unconfirmed_background_note。调用方须保证 sheet 即
    reply.conclusion 本人（_record_reply 在 kind == "urgent" 且
    reply.conclusion 为 dict 时调用）。

    关键时序：visible 在 worry/goal 附加之前捕获，再经
    _strip_close_facts_echo 去回声后传给未回应 goal part 检查——
    回声里的 goal 词不得把 part 判为已回应；检查文本须去掉
    goal_response 引文（见 _append_unaddressed_goals 的中文契约）。
    """
    visible = reply.message
    _append_sheet_worry_goal(reply, sheet)
    # O-19/D-14/D-15: the walk runs against the close text with the
    # collected-facts echo stripped — an echo is not a response, and
    # the echo's own goal quotation must not mark a part addressed
    # (round-3 D3/D5 closes show exactly that: the goal echo carried
    # 护理/开药/后遗症, yet no part sentence was ever rendered). The
    # urgent walk also covers examination parts (O-19 检查), which
    # the shared walk skips as visit parts.
    _append_unaddressed_goals(
        state,
        reply,
        sheet,
        _strip_close_facts_echo(visible),
        include_exam_parts=True,
    )
    _append_uncertainty_note(reply, sheet)
    # ④-4, second option (gate untouched): when the urgent close
    # lands before background questions were asked, say so
    # generically — background names only, no new clinical content —
    # so the offline evaluation knows what to confirm first.
    _append_unconfirmed_background_note(state, reply)
