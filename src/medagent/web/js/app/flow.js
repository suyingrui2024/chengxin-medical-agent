// app.js — flow section (split for maintainability).

import { replyEnvelope } from "../structured/structured-ui.js";
import { renderLocationChips } from "../structured/location-answer-ui.js";
import {
  $,
  historyUI,
  reportDraftUI,
  handoffUI,
  emptyState,
  taskProgress,
  composerDrafts,
  subjectDrafts,
  structuredUI,
  names,
  sourceNames,
  phaseNames,
  state,
  el,
  button,
  toast,
  api,
  factLabel,
  displayFact,
} from "./context.js";
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
} from "./render.js";
import {
  refreshConclusionCard,
  loadEvents,
  appendCalculation,
  renderInspection,
  openEdit,
} from "./views.js";

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
  reportDraftUI.renderItems(data.report_items || []);
  updateComposer();
  (data.calculations || []).forEach(appendCalculation);
  syncTaskFocus();
  renderInspection();
  syncSummaryEdits();
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

// 使用体验评测 F3（2026-11-04）：中途刷新曾落在开始页。只记住**本浏览器**
// 最后进行到的会话——刷新后回到它；测试与别的标签页不受影响。
const LAST_SESSION_KEY = "medagent:last-session-id";

function rememberLastSession(id) {
  try {
    if (id) localStorage.setItem(LAST_SESSION_KEY, id);
    else localStorage.removeItem(LAST_SESSION_KEY);
  } catch {
    /* 隐私模式等无 localStorage 的场景：刷新恢复不可用，其余不受影响 */
  }
}

export function lastSessionId() {
  try {
    return localStorage.getItem(LAST_SESSION_KEY);
  } catch {
    return null;
  }
}

// All conversation entry points share the same reset. Draft maps remain
// keyed by session; transient report/export data belongs to the active one.
export function activateSession(id, events = []) {
  const changingSession = state.sessionId !== id;
  if (changingSession) {
    if (id) rememberComposerDraft();
    reportDraftUI.reset();
    handoffUI.reset();
    Object.assign(state, {
      lastCorrection: null,
      editing: null,
    });
  }
  Object.assign(state, {
    sessionId: id,
    reply: null,
    events,
    taskHistory: false,
  });
  state.shownCalculations.clear();
  rememberLastSession(id);
  $("#messages").replaceChildren();
  $("#welcome").hidden = !!id;
  if (!id) state.case = null;
  return changingSession;
}

export async function startSession() {
  if (state.busy) return;
  state.starting = true;
  setBusy(true);
  try {
    const data = await api("/api/sessions", { method: "POST", body: {} });
    const firstDraft = !state.sessionId ? $("#message-input").value : "";
    activateSession(data.session_id);
    if (firstDraft) composerDrafts.set(state.sessionId, firstDraft);
    renderCase(data.state);
    renderReply(data.reply);
    restoreComposerDraft(state.sessionId);
    navigate("consultation");
    await Promise.all([historyUI.refresh(), loadEvents()]);
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
      $("#input-count").textContent = "0 字";
      $("#composer-length-hint").hidden = true;
      composerDrafts.delete(sessionId);
    }
    if (!choiceValue) subjectDrafts.delete(sessionId);
    applyPatientResponse(data, sessionId);
    await Promise.all([historyUI.refresh(), loadEvents()]);
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
    await Promise.all([historyUI.refresh(), loadEvents()]);
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
    const changingSession = activateSession(id, data.events);
    let latest = null;
    for (const event of data.events) {
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
    historyUI.renderRecent();
    if (!force) navigate("consultation");
  } finally {
    if (!wasBusy) setBusy(false);
  }
}

export function onSessionDeleted(id) {
  composerDrafts.delete(id);
  subjectDrafts.delete(id);
  if (id !== state.sessionId) return;
  state.pendingMessages.clear();
  activateSession(null);
  $("#composer-context").hidden = false;
  $("#open-report-draft").hidden = true;
  $("#open-handoff").hidden = true;
  document.body.classList.remove("has-session");
  renderInspection();
  navigate("consultation");
}
