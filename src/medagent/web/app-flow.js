// app.js — flow section (split for maintainability).

import { replyEnvelope } from "./structured-ui.js";
import { renderLocationChips } from "./location-answer-ui.js";
import {
  $,
  taskProgress,
  composerDrafts,
  subjectDrafts,
  structuredUI,
  names,
  sourceNames,
  modeNames,
  phaseNames,
  state,
  el,
  icon,
  button,
  toast,
  api,
  factLabel,
  displayFact,
  formatTime,
  runtime,
} from "./app-context.js";
import {
  setBusy,
  syncEventPolling,
  scrollBottom,
  focusCurrentAction,
  navigate,
  appendMessage,
  summaryContext,
  syncSummaryEdits,
  renderReply,
  syncTaskFocus,
  renderChoices,
  updateComposer,
  enforceBannerPriority,
  syncBaselineBanner,
} from "./app-render.js";
import {
  refreshFollowUps,
  refreshConclusionCard,
  loadEvents,
  appendCalculation,
  renderInspection,
  openEdit,
} from "./app-views.js";
import { deleteSession } from "./app-settings.js";

export function renderCase(data) {
  if (
    state.case?.session_id === data.session_id &&
    data.revision < state.case.revision
  )
    return;
  if (
    state.case?.session_id === data.session_id &&
    state.case.active_task &&
    !data.active_task &&
    data.suspended_self_check?.id !== state.case.active_task
  ) {
    const prefix = `${data.session_id}:${state.case.active_task}:`;
    for (const key of taskProgress.keys())
      if (key.startsWith(prefix)) taskProgress.delete(key);
  }
  state.case = data;
  renderConversationSubtitle();
  syncBaselineBanner(data);
  const retriageBanner = $("#retriage-banner");
  const priorCase = data.runtime_snapshot?.prior_case;
  retriageBanner.hidden = !data.runtime_snapshot?.follow_up_of;
  enforceBannerPriority();
  if (priorCase?.chief_complaint) {
    const outcomeText =
      { worsened: "加重了", hospitalized: "已经住院" }[priorCase.outcome] ||
      "有变化";
    const lastTime = priorCase.disposition_text
      ? `上次的主要问题是「${priorCase.chief_complaint}」，当时的结论是：${priorCase.disposition_text}`
      : `上次的主要问题是「${priorCase.chief_complaint}」`;
    retriageBanner.textContent = `本次问诊由随访回流发起。${lastTime}你这次反馈情况${outcomeText}，下面重新分诊一次。`;
  }
  $("#urgent-help").hidden = data.phase === "closed";
  document.body.classList.add("has-session");
  $("#open-report-draft").hidden = false;
  $("#open-handoff").hidden = false;
  $("#welcome").hidden = true;
  // S1: the composer hint is shown once before a session starts only.
  $("#composer-context").hidden = true;
  const phase = data.phase;
  const risk = data.risk?.level;
  const status =
    risk === "emergency"
      ? "立即求助"
      : risk === "urgent"
        ? "尽快评估"
        : phaseNames[phase] || "进行中";
  $("#phase-label").textContent = status;
  syncEventPolling();
  const activities = {
    opening: ["先了解主要问题", "先说最困扰你的事，再按需要补充信息。"],
    emergency_screen: [
      "确认是否需要优先求助",
      "先核实紧急线索，再决定是否继续普通问诊。",
    ],
    history: ["了解症状经过", "弄清起病、变化和伴随情况，减少遗漏。"],
    background: [
      "补充相关背景",
      "了解已经采取的处理，以及相关病史和过敏信息。",
    ],
    observation: [
      "记录一次定向观察",
      "图示帮助描述当前情况；做不到或不确定时可以跳过。",
    ],
    summary: ["请核对整理的内容", "检查是否记录准确，有遗漏可以继续补充。"],
    closed: ["本次会谈已结束", "可以回看记录。新的问题请开始一次新问诊。"],
  };
  const activity = ["emergency", "urgent"].includes(risk)
    ? ["优先联系医疗帮助", "当前提示需要优先求助，不必等待信息全部补齐。"]
    : activities[phase] || activities.history;
  // The drawer no longer carries a current-step block (S1 simplification);
  // the step is already shown in the conversation itself.
  const activityTitle = $("#activity-title");
  if (activityTitle) activityTitle.textContent = activity[0];
  const activityReason = $("#activity-reason");
  if (activityReason) activityReason.textContent = activity[1];
  const facts = Object.entries(data.facts);
  $("#fact-count").textContent = String(facts.length);
  $("#facts-count-label").textContent = `${facts.length} 项`;
  $("#fact-list").replaceChildren();
  if (!facts.length)
    $("#fact-list").append(emptyState("信息将随会谈逐步记录。"));
  for (const [key, fact] of facts) {
    const row = el("div", "fact-row");
    const top = el("div", "fact-top");
    top.append(el("span", "", factLabel(key)));
    if (key === "symptom_location") {
      const context = summaryContext(
        false,
        `${data.session_id}:${data.revision}`,
      );
      const edit = button("修改定位", "fact-edit location-revision-start", () =>
        context.onReviseLocation(fact.fact_id),
      );
      edit.dataset.actionId = context.actionId;
      edit.dataset.factId = fact.fact_id;
      edit.hidden = true;
      top.append(edit);
    }
    if (
      key !== "emergency_screen" &&
      names[key] &&
      !data.runtime_snapshot?.composition?.capabilities.some(
        (item) => item.task?.result_key === key,
      )
    )
      top.append(button("更正", "fact-edit", () => openEdit(key, fact)));
    row.append(
      top,
      el(
        "p",
        `fact-value ${fact.assertion === "unknown" ? "unknown" : ""}`,
        key === "consult_for" && data.consultation_subject
          ? data.consultation_subject.label
          : displayFact(key, fact),
      ),
      el("span", "fact-source", sourceNames[fact.source] || fact.source),
    );
    if (fact.structured_input?.schema === "location-answer/v1")
      row
        .querySelector(".fact-value")
        .replaceChildren(renderLocationChips(fact.structured_input.answers));
    $("#fact-list").append(row);
  }
  renderReportItems(data.report_items || []);
  updateComposer();
  (data.calculations || []).forEach(appendCalculation);
  syncTaskFocus();
  renderInspection();
  syncSummaryEdits();
}
export function emptyState(message) {
  const node = el("div", "empty-state compact");
  node.append(icon("cards"), el("p", "", message));
  return node;
}
export function renderReportItems(items) {
  const summary = $("#report-items-summary");
  summary.replaceChildren();
  summary.hidden = !items.length;
  if (!items.length) return;
  const heading = el("div", "report-items-heading");
  heading.append(
    el("span", "", "本次问诊使用的数据"),
    el("small", "", `${items.length} 项`),
  );
  const list = el("div", "report-items-list");
  items.forEach((item) => {
    const row = el("div", "report-item-row");
    const value = [item.reported_value, item.unit].filter(Boolean).join(" ");
    const provenance =
      item.source === "patient_record"
        ? [
            item.record_title,
            item.measured_at && formatTime(item.measured_at, true),
          ]
            .filter(Boolean)
            .join(" · ")
        : "";
    const label = [value, provenance].filter(Boolean).join(" · ");
    row.append(el("strong", "", item.name), el("span", "", label || "已确认"));
    if (item.interpretation_note)
      row.append(el("small", "report-item-note", item.interpretation_note));
    row.append(
      button("移除", "fact-edit", () => removeReportItem(item.report_item_id)),
    );
    list.append(row);
  });
  summary.append(heading, list);
}
export function reportItemText(item) {
  const parts = [
    item.name,
    item.reported_value,
    item.unit,
    item.reference_range,
  ].filter(Boolean);
  if (item.source === "patient_record") {
    if (item.record_title && item.record_title !== item.name)
      parts.push(item.record_title);
    if (item.measured_at) parts.push(formatTime(item.measured_at, true));
  }
  const text = parts.join(" · ");
  return item.interpretation_note
    ? `${text}（解读：${item.interpretation_note}）`
    : text;
}
export function renderReportDraft(draft) {
  const target = $("#report-draft-result");
  target.replaceChildren();
  if (!draft) return;
  if (draft.missing_context?.length)
    target.append(
      el("p", "report-note", `还需要：${draft.missing_context.join("、")}`),
    );
  if (!draft.items?.length) {
    target.append(
      el(
        "p",
        "report-note",
        "未识别到可逐项核对的内容。可尝试按“项目 数值 单位 参考范围”分行粘贴。",
      ),
    );
    return;
  }
  target.append(el("h3", "report-result-title", "逐项确认"));
  const list = el("div", "report-draft-list");
  draft.items.forEach((item) => {
    const row = el("div", "report-draft-row");
    row.append(el("strong", "", item.name), el("p", "", reportItemText(item)));
    if (item.source_excerpt)
      row.append(el("small", "", `原文：${item.source_excerpt}`));
    const confirmed = item.status === "confirmed";
    const confirm = button(
      confirmed ? "已确认" : "确认加入",
      "secondary-button",
      () => confirmReportItem(draft.draft_id, item.source_item_id),
    );
    confirm.dataset.confirmed = String(confirmed);
    confirm.disabled = confirmed || state.busy;
    row.append(confirm);
    list.append(row);
  });
  target.append(list);
}
export async function openReportDraft() {
  if (!state.sessionId) return;
  $("#report-feedback").textContent = "";
  $("#report-dialog").showModal();
  $("#report-text").focus();
}
export async function submitReportDraft(event) {
  event.preventDefault();
  if (!state.sessionId || state.busy) return;
  const text = $("#report-text").value.trim();
  if (!text) return;
  const feedback = $("#report-feedback");
  feedback.textContent = "";
  setBusy(true);
  try {
    const data = await api(`/api/sessions/${state.sessionId}/report-drafts`, {
      method: "POST",
      body: { text, format: $("#report-format").value },
    });
    state.reportDraft = data.draft;
    renderReportDraft(data.draft);
    feedback.textContent = data.draft.items.length
      ? "请核对每一项后再加入本次记录。"
      : "尚未写入任何问诊记录。";
  } catch (error) {
    feedback.textContent = error.message;
    feedback.classList.add("error");
  } finally {
    setBusy(false);
  }
}
export async function confirmReportItem(draftId, sourceItemId) {
  if (!state.sessionId || state.busy) return;
  setBusy(true);
  try {
    const data = await api(
      `/api/sessions/${state.sessionId}/report-drafts/${draftId}/confirm`,
      { method: "POST", body: { source_item_id: sourceItemId } },
    );
    renderCase(data.state);
    if (state.reportDraft?.draft_id === draftId) {
      const item = state.reportDraft.items.find(
        (entry) => entry.source_item_id === sourceItemId,
      );
      if (item) item.status = "confirmed";
      renderReportDraft(state.reportDraft);
    }
    toast("已加入本次记录。可随时移除。");
  } catch (error) {
    toast(error.message);
  } finally {
    setBusy(false);
  }
}
export async function removeReportItem(itemId) {
  if (!state.sessionId || state.busy) return;
  if (!window.confirm("从本次记录中移除此报告项目？")) return;
  setBusy(true);
  try {
    const data = await api(
      `/api/sessions/${state.sessionId}/report-items/${itemId}`,
      {
        method: "DELETE",
      },
    );
    renderCase(data.state);
    toast("已移除该报告项目。");
  } catch (error) {
    toast(error.message);
  } finally {
    setBusy(false);
  }
}
export function handoffValue(value) {
  return typeof value === "object"
    ? JSON.stringify(value)
    : String(value ?? "");
}
export function handoffLines(handoff) {
  const rows = handoff.facts.map(
    (item) => `${item.label}：${handoffValue(item.value)}`,
  );
  if (handoff.report_items.length) {
    rows.push("本次问诊使用的数据：");
    handoff.report_items.forEach((item) =>
      rows.push(`- ${reportItemText(item)}`),
    );
  }
  if (handoff.needs_confirmation.length)
    rows.push(
      `待补充：${handoff.needs_confirmation.map((item) => item.label).join("、")}`,
    );
  return [handoff.title, ...rows, handoff.limitations].join("\n");
}
export function renderHandoff(handoff, renderDialog = true) {
  state.handoffText = handoffLines(handoff);
  if (!renderDialog) return;
  const target = $("#handoff-content");
  target.replaceChildren();
  const addSection = (title, items, renderer) => {
    if (!items.length) return;
    const section = el("section", "handoff-section");
    section.append(el("h3", "", title));
    const list = el("div", "handoff-list");
    items.forEach((item) => list.append(renderer(item)));
    section.append(list);
    target.append(section);
  };
  addSection("已记录", handoff.facts, (item) =>
    el("p", "", `${item.label}：${handoffValue(item.value)}`),
  );
  addSection("本次问诊使用的数据", handoff.report_items, (item) =>
    el("p", "", reportItemText(item)),
  );
  addSection("仍待补充", handoff.needs_confirmation, (item) =>
    el("p", "", item.label),
  );
  target.append(el("p", "handoff-limitation", handoff.limitations));
}
export async function openHandoff() {
  if (!state.sessionId) return;
  try {
    const data = await api(`/api/sessions/${state.sessionId}/handoff`);
    // 渲染单源（模块化重整 §4.4）：方向/科室可见性只消费交接端点
    // 透传的 mode/flag，不再从 live sheet 推导、不再补写 flag，更不再
    // 回落为“无 live sheet 时有方向即视为可见”（该回落分支已删除）。
    // 端点输出无方向即不可见；flag 缺失时按不可见处理
    // （fail-closed，打印模块的 === true 判断兜底）。narrative 仍
    // 取自 live sheet（端点不携带），但 reasoning 只按端点 mode 抄写。
    // 对话框渲染仍用原始端点对象（见上）。
    const sheet = state.case?.conclusion || null;
    const rawConclusion = data.handoff?.conclusion || null;
    // v1.3 §13.6：抄写层与结论卡同源，按端点透传的 directions_mode
    // 门控（与打印模块同一判定；抄写层再收紧一道，不信任上游已
    // 剥离）：restricted/on 方向可见、仅 on 推理线可见、off 方向
    // 整体不抄（置 null，保持端点 off 形态）；mode 缺失的旧端点
    // 回落到 directions_visible 的全量含义；未知 mode 按不可见
    // 处理。科室 hint 仅当 department_visible === true 时抄写；
    // 带话段原样透传（有项才渲染是打印层的规则）。
    const handoffMode = rawConclusion?.directions_mode;
    const hasHandoffMode =
      typeof handoffMode === "string" && handoffMode !== "";
    const directionsShown = hasHandoffMode
      ? handoffMode === "restricted" || handoffMode === "on"
      : rawConclusion?.directions_visible === true;
    const reasoningShown = hasHandoffMode
      ? handoffMode === "on"
      : rawConclusion?.directions_visible === true;
    // M2 纵深防御：narrative.reasoning 逐条含方向标签，仅全量模式
    // 抄进 state.handoff（打印模块另有同门控，双重保险）；framing /
    // severity 不含方向，可保留。
    const narrative = sheet?.narrative
      ? {
          framing: sheet.narrative.framing,
          severity: sheet.narrative.severity,
          ...(reasoningShown ? { reasoning: sheet.narrative.reasoning } : {}),
        }
      : undefined;
    let copiedConclusion = null;
    if (rawConclusion) {
      copiedConclusion = {
        ...rawConclusion,
        narrative,
        directions: directionsShown ? rawConclusion.directions : null,
      };
      if (
        rawConclusion.department_visible !== true &&
        rawConclusion.disposition
      ) {
        const restDisposition = { ...rawConclusion.disposition };
        delete restDisposition.department_hint;
        copiedConclusion.disposition = restDisposition;
      }
    }
    state.handoff = data.handoff
      ? {
          ...data.handoff,
          conclusion: copiedConclusion,
        }
      : null;
    renderHandoff(data.handoff);
    $("#handoff-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}
export async function copyHandoff() {
  if (!state.handoffText) return;
  try {
    await navigator.clipboard.writeText(state.handoffText);
    toast("摘要已复制。");
  } catch {
    const area = el("textarea");
    area.value = state.handoffText;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    toast("摘要已复制。");
  }
}
export function renderConversationSubtitle(backend = state.settings?.backend) {
  const subject = state.case?.consultation_subject;
  const prepared = preparedSubjectDraft();
  const actualBackend =
    state.case?.runtime_snapshot?.transport?.adapter ||
    state.sessions.find((item) => item.session_id === state.sessionId)
      ?.backend ||
    backend;
  const mode = modeNames[actualBackend] || "问诊";
}

export function preparedSubjectDraft() {
  return state.reply?.kind === "question" &&
    state.case &&
    !Object.keys(state.case.facts).length
    ? subjectDrafts.get(state.sessionId)
    : null;
}
export function rememberComposerDraft() {
  if (state.sessionId)
    composerDrafts.set(state.sessionId, $("#message-input").value);
}
export function restoreComposerDraft(id) {
  $("#message-input").value = composerDrafts.get(id) || "";
  $("#message-input").dispatchEvent(new Event("input"));
}

export async function startSession() {
  if (state.busy) return;
  state.starting = true;
  setBusy(true);
  try {
    const data = await api("/api/sessions", { method: "POST", body: {} });
    const firstDraft = !state.sessionId ? $("#message-input").value : "";
    rememberComposerDraft();
    state.sessionId = data.session_id;
    if (firstDraft) composerDrafts.set(state.sessionId, firstDraft);
    state.reply = null;
    state.taskHistory = false;
    state.shownCalculations.clear();
    state.events = [];
    state.reportDraft = null;
    state.handoffText = "";
    $("#messages").replaceChildren();
    $("#welcome").hidden = true;
    renderCase(data.state);
    renderReply(data.reply);
    restoreComposerDraft(state.sessionId);
    navigate("consultation");
    renderConversationSubtitle(state.settings.backend);
    await Promise.all([refreshSessions(), loadEvents()]);
  } catch (error) {
    toast(`无法开始问诊：${error.message}`);
  } finally {
    state.starting = false;
    setBusy(false);
    focusCurrentAction();
  }
}
export async function sendMessage(
  text,
  choiceValue = null,
  calculation = null,
  observation = null,
) {
  if (state.busy || !text.trim()) return;
  if (!state.sessionId) {
    await startSession();
    if (!state.sessionId) return;
  }
  const submitted = text.trim();
  const composerAtSubmit = $("#message-input").value;
  const sessionId = state.sessionId;
  const epoch = ++state.messageEpoch;
  state.pendingMessages.set(epoch, sessionId);
  setBusy(true);
  appendMessage(submitted, "user");
  renderChoices([]);
  scrollBottom();
  try {
    const data = await api(`/api/sessions/${sessionId}/messages`, {
      method: "POST",
      body: {
        text: submitted,
        choice_value: choiceValue,
        ...(calculation ? { calculation } : {}),
        ...(observation ? { observation } : {}),
        expected_action_id:
          state.reply?.action_id ||
          state.reply?.question_id ||
          state.reply?.task?.id ||
          null,
      },
    });
    if (sessionId !== state.sessionId) return;
    if (
      epoch === state.messageEpoch &&
      (!composerAtSubmit.trim() || composerAtSubmit.trim() === submitted)
    ) {
      $("#message-input").value = "";
      $("#message-input").style.height = "auto";
      $("#input-count").textContent = "0 / 2000";
      composerDrafts.delete(sessionId);
    }
    if (!choiceValue) subjectDrafts.delete(sessionId);
    applyPatientResponse(data, sessionId);
    await Promise.all([refreshSessions(), loadEvents()]);
  } catch (error) {
    if (sessionId !== state.sessionId || epoch !== state.messageEpoch) return;
    appendMessage(
      error.code === "stale_action"
        ? "当前问题已更新，正在恢复最新会谈。"
        : `无法确认这一步是否已保存：${error.message}。请先核对最新记录。`,
      "agent",
      "transport-error",
    );
    try {
      await resumeSession(state.sessionId, true);
    } catch {
      toast("恢复未成功，输入内容仍保留在输入框。");
    }
  } finally {
    state.pendingMessages.delete(epoch);
    if (sessionId === state.sessionId) {
      syncPatientBusy();
      focusCurrentAction();
    }
  }
}

export function syncPatientBusy() {
  const pending = [...state.pendingMessages.values()].includes(state.sessionId);
  setBusy(state.starting || (pending && state.case?.phase !== "closed"));
}

export function applyPatientResponse(data, sessionId) {
  if (state.sessionId !== sessionId || data.state.session_id !== sessionId)
    return;
  if (
    state.case?.session_id === sessionId &&
    data.state.revision < state.case.revision
  )
    return;
  renderCase(data.state);
  state.taskHistory = false;
  if (!data.reply.action_id || state.reply?.action_id !== data.reply.action_id)
    renderReply(data.reply);
}

export async function requestEmergencyHelp(event) {
  event.preventDefault();
  if (!state.sessionId || state.priorityBusy) return;
  const sessionId = state.sessionId;
  const epoch = ++state.messageEpoch;
  const text = $("#urgent-help-input").value.trim() || "我需要紧急帮助";
  state.priorityBusy = true;
  state.pendingMessages.set(epoch, sessionId);
  $("#urgent-help-submit").disabled = true;
  $("#urgent-help-error").hidden = true;
  setBusy(true);
  try {
    const data = await api(`/api/sessions/${sessionId}/messages`, {
      method: "POST",
      body: {
        text,
        choice_value: "request_emergency_help",
        expected_action_id: state.reply?.action_id || null,
      },
    });
    if (state.sessionId !== sessionId) return;
    appendMessage(text, "user");
    applyPatientResponse(data, sessionId);
    $("#urgent-help-dialog").close();
    $("#urgent-help-input").value = "";
    await Promise.all([refreshSessions(), loadEvents()]);
  } catch (error) {
    if (state.sessionId !== sessionId) return;
    $("#urgent-help-error").textContent =
      "尚未确认保存，请重试或核对当前记录。";
    $("#urgent-help-error").hidden = false;
    try {
      await resumeSession(sessionId, true);
    } catch {
      /* Keep this request and error visible. */
    }
  } finally {
    state.priorityBusy = false;
    state.pendingMessages.delete(epoch);
    $("#urgent-help-submit").disabled = false;
    if (state.sessionId === sessionId) {
      syncPatientBusy();
      focusCurrentAction();
    }
  }
}
export async function resumeSession(id, force = false) {
  if (state.busy && !force) {
    toast("请等待当前步骤完成。");
    return;
  }
  const wasBusy = state.busy;
  if (!wasBusy) setBusy(true);
  try {
    const data = await api(`/api/sessions/${id}/snapshot`);
    if (
      id === state.sessionId &&
      state.case &&
      data.state.revision < state.case.revision
    )
      return;
    const trace = { events: data.events };
    const changingSession = state.sessionId !== id;
    if (changingSession) rememberComposerDraft();
    if (changingSession) {
      state.reportDraft = null;
      state.handoffText = "";
    }
    state.sessionId = id;
    state.events = trace.events;
    state.taskHistory = false;
    state.shownCalculations.clear();
    $("#messages").replaceChildren();
    $("#welcome").hidden = true;
    let latest = null;
    for (const event of trace.events) {
      if (event.event_type === "turn.user_message")
        appendMessage(event.payload.text, "user");
      else if (event.event_type === "fact.correction_requested")
        appendMessage(
          `更正${names[event.payload.fact_key] || event.payload.fact_key}：${event.payload.new_text}`,
          "user",
        );
      else if (event.event_type === "capability.completed")
        appendCalculation(event.payload);
      else if (event.event_type === "turn.agent_reply" && event.payload.reply) {
        renderReply(event.payload.reply, { replay: true });
        latest = event.payload.reply;
      }
    }
    state.reply = latest;
    syncTaskFocus();
    renderChoices(latest?.quick_replies || []);
    renderCase(data.state);
    if (changingSession) restoreComposerDraft(id);
    if (latest?.summary && data.state.phase === "summary") {
      const block = structuredUI
        .blocks(replyEnvelope(latest))
        .find((item) => item.type === "summary");
      const cards = $("#messages").querySelectorAll(
        '[data-block-type="summary"]',
      );
      if (block && cards.length)
        cards[cards.length - 1].replaceWith(
          structuredUI.render(block, summaryContext()),
        );
    }
    // Replayed replies carry the conclusion as it was when the session
    // closed; enrichment may have completed since, so overlay the
    // current sheet (with further_reading) onto the replayed card.
    if (data.state.phase === "closed")
      refreshConclusionCard(data.state.conclusion);
    syncSummaryEdits();
    scrollBottom();
    const meta = state.sessions.find((item) => item.session_id === id);
    renderConversationSubtitle(meta?.backend);
    renderRecent();
    if (!force) navigate("consultation");
  } finally {
    if (!wasBusy) setBusy(false);
  }
}
export let sessionsGeneration = 0;
export async function refreshSessions() {
  // Overlapping refreshes (send/start/delete/manual) can resolve out
  // of order; only the newest generation may overwrite the list.
  const generation = ++sessionsGeneration;
  const data = await api("/api/sessions");
  if (generation !== sessionsGeneration) return;
  state.sessions = data.sessions;
  renderRecent();
  renderHistory();
  await refreshFollowUps();
}
export function renderRecent() {
  $("#history-count").textContent = String(state.sessions.length);
  $("#recent-count").textContent = String(state.sessions.length);
  $("#recent-sessions").replaceChildren();
  if (!state.sessions.length) {
    $("#recent-sessions").append(
      el("p", "sidebar-empty", "问诊记录会出现在这里"),
    );
    return;
  }
  state.sessions.slice(0, 8).forEach((item) => {
    const label =
      item.subject_label || subjectDrafts.get(item.session_id)?.label;
    const title = label
      ? `${label}${item.subject_label ? "" : "（待发送）"} · ${item.title}`
      : item.title;
    const node = button(
      title,
      `recent-item ${item.session_id === state.sessionId ? "active" : ""}`,
      () =>
        resumeSession(item.session_id).catch((error) => toast(error.message)),
    );
    node.dataset.sessionId = item.session_id;
    node.title = `${title} · ${formatTime(item.created_at, true)}`;
    node.disabled = state.busy;
    $("#recent-sessions").append(node);
  });
}
export function renderHistory() {
  $("#history-list").replaceChildren();
  if (!state.sessions.length) {
    $("#history-list").append(
      emptyState("还没有会谈记录。开始一次问诊后，会在这里显示。"),
    );
    return;
  }
  for (const item of state.sessions) {
    const card = button("", "history-card", () =>
      resumeSession(item.session_id).catch((error) => toast(error.message)),
    );
    card.dataset.sessionId = item.session_id;
    const emblem = el("span", "history-icon");
    emblem.append(icon("chat"));
    const info = el("div", "history-info");
    info.append(
      el(
        "strong",
        "",
        item.subject_label
          ? `${item.subject_label} · ${item.title}`
          : subjectDrafts.has(item.session_id)
            ? `${subjectDrafts.get(item.session_id).label}（待发送） · ${item.title}`
            : item.title,
      ),
      el(
        "small",
        "",
        `${formatTime(item.created_at, true)} · ${modeNames[item.backend] || item.backend} · ${item.fact_count} 项记录`,
      ),
    );
    card.append(
      emblem,
      info,
      el("span", "subtle-pill", phaseNames[item.phase] || item.phase),
      icon("arrow"),
    );
    card.disabled = state.busy;
    const row = el("div", "history-card-row");
    const deleteButton = el("button", "text-button history-delete", "删除");
    deleteButton.type = "button";
    deleteButton.disabled = state.busy;
    deleteButton.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(item.session_id).catch((error) => toast(error.message));
    });
    row.append(card, deleteButton);
    $("#history-list").append(row);
  }
}
export const FOLLOW_UP_KIND_LABELS = {
  urgent_checkin: "紧急确认",
  routine_checkin: "例行回访",
};
runtime.renderCaseImpl = renderCase;
