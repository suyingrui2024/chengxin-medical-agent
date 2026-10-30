// app.js — render section (split for maintainability).

import { replyEnvelope } from "./structured-ui.js";
import { createBaselineBanner } from "./patient-records-ui.js";
import {
  $,
  calculationDrafts,
  interactionDrafts,
  subjectDrafts,
  structuredUI,
  names,
  values,
  state,
  executionUI,
  patientRecordsUI,
  el,
  button,
  positionToast,
  toast,
  api,
  runtime,
} from "./app-context.js";
import {
  renderCase,
  renderConversationSubtitle,
  preparedSubjectDraft,
  startSession,
  sendMessage,
  refreshSessions,
  renderRecent,
  renderHistory,
} from "./app-flow.js";
import {
  followUpDueCount,
  focusFollowUpCard,
  renderLibrary,
  loadEvents,
  refreshCapabilities,
  setCapabilityBusy,
  renderInspection,
  openEdit,
  restoreCorrection,
} from "./app-views.js";
import { refreshSubagentPanel, init } from "./app-settings.js";

export let pollTimer = null;
export function setBusy(busy) {
  state.busy = busy;
  $("#send-button").disabled = busy;
  $("#new-session").disabled = busy;
  $("#start-consultation").disabled = busy;
  $("#message-input").disabled = busy;
  $("#urgent-help").disabled = state.priorityBusy || !state.sessionId;
  $("#open-report-draft").disabled = busy || !state.sessionId;
  $("#open-handoff").disabled = busy || !state.sessionId;
  $("#busy-indicator").hidden = !busy;
  $("#messages").setAttribute("aria-busy", String(busy));
  document
    .querySelectorAll(
      ".quick-replies button,.fact-edit,.recent-item,.history-card,.starter-link",
    )
    .forEach((node) => {
      node.disabled = busy;
    });
  document
    .querySelectorAll(".report-draft-row .secondary-button")
    .forEach((node) => {
      node.disabled = busy || node.dataset.confirmed === "true";
    });
  syncEventPolling();
  if (busy && state.sessionId) {
    scrollBottom();
  }
  syncSummaryEdits();
  document
    .querySelectorAll(
      ".visual-choice,#active-question .calculation-form input,#active-question .calculation-form select,#active-question .calculation-form button,#active-question .calculation-continue",
    )
    .forEach((node) => {
      node.disabled = busy;
    });
  document
    .querySelectorAll(".severity-options button,.body-pin")
    .forEach((node) => {
      node.disabled = busy || node.dataset.actionable !== "true";
    });
  document.querySelectorAll(".body-region-target").forEach((node) => {
    const disabled = busy || node.dataset.actionable !== "true";
    node.setAttribute("aria-disabled", String(disabled));
    node.setAttribute(
      "tabindex",
      disabled || node.hasAttribute("hidden") ? "-1" : "0",
    );
  });
  document
    .querySelectorAll(
      ".severity-confirm,.map-confirm,.body-proposal-edit,.step-controls button,.location-answer-control,.location-area-input,.location-answer-note",
    )
    .forEach((node) => {
      node.disabled = busy || node.dataset.ready !== "true";
    });
  $("#active-question").setAttribute("aria-busy", String(busy));
  $("#focus-label").textContent = busy
    ? "正在整理…"
    : state.reply?.task
      ? "按图观察 · 可以跳过"
      : document.body.classList.contains("summary-focused")
        ? state.case?.phase === "closed"
          ? "结论与后续安排"
          : "核对记录 · 可以更正"
        : "当前这一步";
}
// Education enrichment lands seconds after a session closes, but the
// poller normally stops with the session, so the completion events may
// never be observed. Keep polling briefly for a closed session whose
// conclusion is still waiting on further_reading — bounded, so a
// session whose enrichment never runs (web tools off, directions
// hidden) does not poll forever.
export const EDUCATION_POLL_LIMIT = 30;
export let educationWatchKey = null;
export let educationPollsLeft = 0;
export function educationPending() {
  const conclusion = state.case?.conclusion;
  if (state.case?.phase !== "closed" || !conclusion) return false;
  return conclusion.further_reading?.status !== "ready";
}
export function syncEventPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
  const reviewActive = ["queued", "running"].includes(
    state.case?.background_review?.status,
  );
  let educationActive = false;
  if (educationPending()) {
    const key = `${state.sessionId}:${state.case.conclusion.revision ?? ""}`;
    if (educationWatchKey !== key) {
      educationWatchKey = key;
      educationPollsLeft = EDUCATION_POLL_LIMIT;
    }
    educationActive = educationPollsLeft > 0;
  } else {
    educationWatchKey = null;
    educationPollsLeft = 0;
  }
  if (state.sessionId && (state.busy || reviewActive || educationActive))
    pollTimer = setInterval(() => {
      if (educationActive && !state.busy && !reviewActive) {
        educationPollsLeft -= 1;
        if (educationPollsLeft <= 0) syncEventPolling();
      }
      loadEvents().catch(() => {});
    }, 1100);
}
export function scrollBottom() {
  const scroll = $("#conversation-scroll");
  requestAnimationFrame(() => {
    const pending = [
      ...scroll.querySelectorAll(
        '.interaction-followup[data-status="pending"]',
      ),
    ].at(-1);
    if (
      state.reply?.calculation_followup?.status === "pending" &&
      !state.taskHistory &&
      pending &&
      !scroll.hidden
    ) {
      scroll.scrollTop +=
        pending.getBoundingClientRect().top -
        scroll.getBoundingClientRect().top -
        12;
      return;
    }
    scroll.scrollTop = document.body.classList.contains("summary-focused")
      ? 0
      : scroll.scrollHeight;
  });
}
export function focusCurrentAction() {
  const target =
    state.reply?.task && state.reply?.input_error
      ? $("#active-task .task-feedback")
      : state.reply?.calculation_input || state.reply?.calculation_result
        ? $("#active-question h2,#active-question h3")
        : state.case?.phase === "summary"
          ? $("#messages .current-summary h3")
          : state.reply?.task
            ? $("#active-task h3")
            : $("#active-question h2,#active-question h3");
  if (
    target &&
    (window.innerWidth <= 700 ||
      state.reply?.quick_replies?.length ||
      state.reply?.task ||
      state.reply?.calculation_input ||
      state.reply?.calculation_result ||
      state.case?.phase === "summary")
  ) {
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
    if (window.matchMedia("(max-height: 560px)").matches)
      target.scrollIntoView({ block: "start", inline: "nearest" });
    if (state.reply?.task && state.reply?.input_error) {
      const pane = $("#active-task");
      const bounds = pane.getBoundingClientRect();
      const message = target.getBoundingClientRect();
      if (message.bottom > bounds.bottom)
        pane.scrollTop += message.bottom - bounds.bottom + 8;
      else if (message.top < bounds.top)
        pane.scrollTop -= bounds.top - message.top + 8;
    }
  } else if (!$("#composer").hidden && window.innerWidth > 700)
    $("#message-input").focus({ preventScroll: true });
}
export function navigate(view) {
  if (
    view === "history" &&
    followUpDueCount() > 0 &&
    "Notification" in window &&
    Notification.permission === "default"
  ) {
    // Ask once, from a user gesture, so due follow-ups can notify.
    Notification.requestPermission().catch(() => {});
  }
  if (
    ![
      "consultation",
      "records",
      "history",
      "tasks",
      "inspect",
      "capabilities",
      "execution",
      "settings",
    ].includes(view)
  )
    return;
  if (
    state.workspaceMode !== "developer" &&
    ["tasks", "inspect", "capabilities", "execution"].includes(view)
  )
    view = "consultation";
  const previousView = state.view;
  state.view = view;
  // Keep the URL hash honest: init() deep-links from it, so navigation
  // must write it back or a refresh lands on a stale view.
  try {
    if (location.hash.slice(1) !== view)
      history.replaceState(null, "", `#${view}`);
  } catch {
    /* file:// or sandboxed contexts may refuse history writes */
  }
  $("#workspace-storage-note").textContent =
    view === "execution"
      ? "文件与任务记录保存在本机"
      : view === "records"
        ? "健康档案保存在本机"
        : state.sessionPersistence
          ? "已结束的会谈保存在本机"
          : "会谈仅保留到服务关闭";
  document.querySelectorAll(".view").forEach((node) => {
    const active = node.id === `view-${view}`;
    node.hidden = !active;
    node.classList.toggle("active", active);
  });
  document.querySelectorAll(".nav-item[data-view]").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === view);
    if (node.dataset.view === view) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  $("#page-title").textContent = {
    consultation: "问诊",
    records: "健康档案",
    history: "会谈记录",
    tasks: "图示任务库",
    inspect: "运行检查",
    capabilities: "能力与模板",
    execution: "执行工作台",
    settings: "设置",
  }[view];
  $("#sidebar").classList.remove("open");
  if (view === "history")
    refreshSessions().catch((error) => toast(error.message));
  if (view === "records")
    patientRecordsUI.refresh().catch((error) => toast(error.message));
  if (view === "tasks") renderLibrary();
  if (view === "settings") refreshSubagentPanel();
  if (view === "execution") executionUI.refresh();
  if (view === "capabilities" && !state.capabilityBusy) {
    // A late catalog refresh must not replace a template the user just chose.
    setCapabilityBusy(true);
    refreshCapabilities()
      .catch((error) => toast(error.message))
      .finally(() => setCapabilityBusy(false));
  }
  if (view === "inspect") {
    renderInspection();
    loadEvents().catch((error) => toast(error.message));
  }
  // Task focus belongs to the consultation view only; re-evaluate on every
  // navigation so leaving mid-task (hash change, back button) restores the shell.
  syncTaskFocus();
  if (!$("#toast").hidden) positionToast();
  // Move focus to the incoming view's heading on a real view change so
  // keyboard and screen-reader users land on the new view instead of a
  // control that just went hidden. (The breadcrumb title cannot serve
  // as the target: the topbar is display:none at desktop widths.)
  if (view !== previousView) {
    const heading = document.querySelector(
      `#view-${view} h1, #view-${view} h2`,
    );
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }
}

export function applyRetentionCopy() {
  if (!state.sessionPersistence) return;
  const historyNote = $("#history-retention-note");
  if (historyNote)
    historyNote.textContent =
      "已结束的问诊与随访计划保存在本机，关闭应用后仍可查看；进行中的问诊请在本次完成。";
  const settingsNote = $("#settings-retention-note");
  if (settingsNote)
    settingsNote.textContent =
      "本机数据：已结束的会谈与健康档案保存在本机，进行中的问诊不跨重启。研究原型 · 能力协议 v1。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。";
}

export function setWorkspaceMode(mode) {
  state.workspaceMode = mode === "developer" ? "developer" : "patient";
  document.body.dataset.mode = state.workspaceMode;
  $("#workspace-mode").value = state.workspaceMode;
  const developer = state.workspaceMode === "developer";
  $("#workspace-mode-description").textContent = developer
    ? "显示患者流程和全部研发测试功能"
    : "只显示患者使用的功能";
  $("#nav-section-label").textContent = developer ? "全部功能" : "患者功能";
  $("#local-data-label").textContent = developer ? "本机测试数据" : "本机数据";
  $("#breadcrumb-root").textContent = developer ? "完整研发" : "患者服务";
  navigate(state.view);
}

export function appendMessage(text, role, kind = "") {
  const row = el("div", `message ${role} ${kind}`);
  const avatar = el("span", "message-avatar");
  if (role === "user") avatar.textContent = "你";
  else {
    const image = el("img");
    image.src = "brand.png";
    image.alt = "";
    avatar.append(image);
  }
  row.append(avatar, el("div", "bubble", text));
  $("#messages").append(row);
}
export function renderTask(task, preview = false) {
  return structuredUI.render(
    { id: `task:${task.id}`, type: "self_check", data: task },
    {
      preview,
      sessionId: preview ? "preview" : state.sessionId,
      onAnswer: () => {
        if (!state.busy) $("#message-input").focus();
      },
    },
  );
}
export function patientActionContext() {
  const sessionId = state.sessionId,
    actionId = state.reply?.action_id;
  const current = () =>
    !state.busy &&
    state.sessionId === sessionId &&
    state.reply?.action_id === actionId;
  return {
    isBusy: () => !current(),
    onChoose: (label, value, responseText = label) => {
      if (current()) sendMessage(responseText, value);
    },
    onConfirmAnswer: (text) => {
      if (current()) sendMessage(text);
    },
    onConfirmLocation: (payload) => {
      if (current())
        sendMessage("核对定位答案", "confirm_location_answer", null, payload);
    },
  };
}
export function summaryContext(
  readOnly = false,
  actionId = state.reply?.action_id,
  revisionAction = state.reply?.location_revision_action,
  reviewStatus = null,
  conclusionPresent = false,
) {
  const sessionId = state.sessionId;
  return {
    readOnly,
    reviewStatus,
    conclusionPresent,
    actionId,
    reviewFeedback:
      state.lastCorrection?.actionId === actionId ? state.lastCorrection : null,
    locationRevisionAction: revisionAction,
    onReviseLocation: (factId) => {
      if (
        !readOnly &&
        !state.busy &&
        state.sessionId === sessionId &&
        state.reply?.action_id === actionId &&
        state.reply?.location_revision_action?.fact_id === factId &&
        state.case?.facts.symptom_location?.fact_id === factId
      ) {
        $("#facts-panel").classList.remove("open");
        sendMessage("修改定位", `revise_location:${factId}`);
      }
    },
    onNewSubject: async (text, label) => {
      if (
        !readOnly &&
        !state.busy &&
        state.sessionId === sessionId &&
        state.reply?.action_id === actionId &&
        state.reply?.kind === "subject_change"
      ) {
        await startSession();
        if (state.sessionId && state.sessionId !== sessionId) {
          subjectDrafts.set(state.sessionId, { label });
          $("#message-input").value = text;
          $("#message-input").dispatchEvent(new Event("input"));
          syncTaskFocus();
          renderConversationSubtitle();
          renderRecent();
          renderHistory();
          updateComposer();
          $("#message-input").focus();
        }
      }
    },
    onResumeSubject: () => {
      if (
        !readOnly &&
        !state.busy &&
        state.sessionId === sessionId &&
        state.reply?.action_id === actionId &&
        state.reply?.kind === "subject_change"
      )
        sendMessage("继续当前就诊人的问诊", "resume_current_subject");
    },
    onProcessCalculationFollowup: (id) => {
      if (
        !readOnly &&
        !state.busy &&
        state.sessionId === sessionId &&
        state.reply?.action_id === actionId &&
        state.case?.phase !== "closed"
      )
        sendMessage("继续处理补充", `process_calculation_supplement:${id}`);
    },
    // v1.3 §13.5(a) reminder wiring: the conclusion card's reminder
    // button focuses the already-scheduled follow-up card. Focusing
    // is navigation, not a mutation, so it is offered even for
    // read-only (replayed/refresh) renders.
    onFocusFollowUpCard: focusFollowUpCard,
    canRepeat: () =>
      Boolean(
        !state.busy &&
        state.sessionId === sessionId &&
        state.reply?.action_id === actionId &&
        state.case?.phase !== "closed" &&
        !state.case?.active_task,
      ),
    onRepeat: (taskId) => {
      if (
        !state.busy &&
        state.sessionId === sessionId &&
        state.reply?.action_id === actionId &&
        state.case?.phase !== "closed" &&
        !state.case?.active_task
      )
        sendMessage("再测一次腋温", `repeat_self_check:${taskId}`);
    },
    canEdit: (key) =>
      Boolean(
        names[key] &&
        key !== "emergency_screen" &&
        state.case?.facts[key] &&
        !state.case.runtime_snapshot?.composition?.capabilities.some(
          (item) => item.task?.result_key === key,
        ),
      ),
    onCorrect: (key) => {
      if (
        state.sessionId === sessionId &&
        !state.busy &&
        state.case?.phase === "summary"
      )
        openEdit(key, state.case.facts[key]);
    },
    onUndoCorrection: (correction) => restoreCorrection(correction),
  };
}
export function syncSummaryEdits() {
  document.querySelectorAll(".location-revision-start").forEach((node) => {
    node.hidden =
      node.dataset.factId !== state.reply?.location_revision_action?.fact_id;
    node.disabled =
      state.busy ||
      node.dataset.actionId !== state.reply?.action_id ||
      node.dataset.factId !== state.reply?.location_revision_action?.fact_id;
  });
  document.querySelectorAll(".subject-action").forEach((node) => {
    node.disabled =
      state.busy ||
      state.case?.phase === "closed" ||
      node.dataset.actionId !== state.reply?.action_id;
  });
  document.querySelectorAll(".followup-process").forEach((node) => {
    node.disabled =
      state.busy ||
      state.case?.phase === "closed" ||
      node.dataset.actionId !== state.reply?.action_id;
  });
  const block =
    state.reply &&
    structuredUI
      .blocks(replyEnvelope(state.reply))
      .find((item) => item.type === "summary");
  const observationBlock =
    state.reply &&
    structuredUI
      .blocks(replyEnvelope(state.reply))
      .find((item) => item.type === "observation_series");
  document.querySelectorAll(".summary-edit").forEach((node) => {
    node.disabled =
      state.busy ||
      state.case?.phase !== "summary" ||
      node.closest("[data-block-id]")?.dataset.blockId !== block?.id;
  });
  document.querySelectorAll(".observation-repeat").forEach((node) => {
    node.disabled =
      state.busy ||
      state.case?.phase === "closed" ||
      Boolean(state.case?.active_task) ||
      ![block?.id, observationBlock?.id].includes(
        node.closest("[data-block-id]")?.dataset.blockId,
      );
  });
}
export function renderReply(reply, { replay = false } = {}) {
  if (!replay && state.lastCorrection?.actionId !== reply.action_id)
    state.lastCorrection = null;
  const replyBlocks = structuredUI.blocks(replyEnvelope(reply));
  const conclusionPresent = replyBlocks.some(
    (block) => block.type === "conclusion",
  );
  for (const block of replyBlocks) {
    if (
      ![
        "self_check",
        "question",
        "calculation_input",
        "calculation",
        "subject_change",
      ].includes(block.type)
    )
      $("#messages").append(
        structuredUI.render(
          block,
          summaryContext(
            replay || reply.phase === "closed",
            reply.action_id,
            reply.location_revision_action,
            block.type === "summary" ? block.data.review_status : null,
            conclusionPresent,
          ),
        ),
      );
  }
  if (reply.task)
    $("#messages").append(
      el("p", "task-milestone", `已提供图示任务：${reply.task.title}`),
    );
  if (!replay) {
    state.reply = reply;
    state.taskHistory = false;
    syncTaskFocus();
    renderChoices(reply.quick_replies || []);
    updateComposer();
    syncSummaryEdits();
    scrollBottom();
  }
}
export function syncTaskFocus() {
  const supplementPending =
    state.reply?.kind === "service_error" &&
    (state.reply?.observation_result?.supplement?.status === "pending" ||
      state.reply?.calculation_followup?.status === "pending");
  if (supplementPending && state.case?.phase !== "closed") {
    $("#view-consultation h1").textContent = "补充信息待处理";
    $("#phase-label").textContent = "补充待处理";
  }
  const task = state.reply?.task;
  const active = Boolean(
    task &&
    state.view === "consultation" &&
    state.case?.active_task === task.id &&
    state.case?.phase !== "closed",
  );
  document.body.classList.toggle(
    "task-focused",
    active && !state.taskHistory && state.workspaceMode === "patient",
  );
  document.body.classList.toggle(
    "location-focused",
    active && task.id === "body_location" && !state.taskHistory,
  );
  const question =
    state.reply &&
    structuredUI
      .blocks(replyEnvelope(state.reply))
      .find((block) =>
        [
          "question",
          "calculation_input",
          "calculation",
          "subject_change",
        ].includes(block?.type),
      );
  const focusedQuestion = Boolean(
    question && !active && state.case?.phase !== "closed",
  );
  const observation =
    state.reply &&
    structuredUI
      .blocks(replyEnvelope(state.reply))
      .find((block) => block.type === "observation_series");
  $("#active-observation").hidden =
    !focusedQuestion || state.taskHistory || !observation;
  if (
    observation &&
    focusedQuestion &&
    $("#active-observation").dataset.identity !== observation.id
  ) {
    $("#active-observation").replaceChildren(
      structuredUI.render(observation, {
        ...summaryContext(false, state.reply.action_id),
        compactObservation: true,
      }),
    );
    $("#active-observation").dataset.identity = observation.id;
  } else if (!observation || !focusedQuestion) {
    $("#active-observation").replaceChildren();
    delete $("#active-observation").dataset.identity;
  }
  const summaryFocus = Boolean(
    !question &&
    state.reply?.calculation_followup?.status !== "pending" &&
    (state.reply?.summary || state.reply?.kind === "closed") &&
    state.case &&
    ["summary", "closed"].includes(state.case.phase) &&
    state.case.risk?.level !== "emergency" &&
    $("#messages .summary-card"),
  );
  document
    .querySelectorAll(".summary-card")
    .forEach((card) => card.classList.remove("current-summary"));
  if (summaryFocus)
    [...document.querySelectorAll("#messages .summary-card")]
      .at(-1)
      ?.classList.add("current-summary");
  // The closed view's outcome blocks (conclusion sheet + follow-up card)
  // belong to the focused surface, ahead of the record card; everything
  // else stays in the collapsed transcript.
  document
    .querySelectorAll(".current-outcome")
    .forEach((card) => card.classList.remove("current-outcome"));
  if (summaryFocus && state.case?.phase === "closed")
    for (const selector of [".conclusion-card", ".followup-card"])
      [...document.querySelectorAll(`#messages ${selector}`)]
        .at(-1)
        ?.classList.add("current-outcome");
  document
    .querySelectorAll(".current-summary-message")
    .forEach((node) => node.classList.remove("current-summary-message"));
  const summaryNode = $("#messages .current-summary");
  if (
    summaryNode &&
    (state.reply?.kind === "urgent" ||
      state.reply?.message?.includes("尚未执行"))
  )
    summaryNode.previousElementSibling?.classList.add(
      "current-summary-message",
    );
  document.body.classList.toggle(
    "summary-focused",
    summaryFocus && !state.taskHistory,
  );
  $("#task-focus-bar").hidden = !(active || focusedQuestion || summaryFocus);
  $("#focus-label").textContent = state.busy
    ? "正在整理…"
    : active
      ? "按图观察 · 可以跳过"
      : summaryFocus
        ? state.case.phase === "closed"
          ? "结论与后续安排"
          : "核对记录 · 可以更正"
        : "当前这一步";
  $("#active-task").hidden = !active || state.taskHistory;
  $("#active-question").hidden = !focusedQuestion || state.taskHistory;
  if (!active) {
    $("#active-task").replaceChildren();
    delete $("#active-task").dataset.identity;
  }
  if (!focusedQuestion) {
    $("#active-question").replaceChildren();
    delete $("#active-question").dataset.identity;
  }
  $("#conversation-scroll").hidden =
    (active || focusedQuestion) && !state.taskHistory;
  if (focusedQuestion) {
    const prepared = preparedSubjectDraft();
    const identity = `${state.sessionId}:${question.id}:${prepared ? "draft" : "reply"}`;
    const resumedAction =
      state.reply.developer_trace?.resume_question_action_id;
    if (resumedAction && !interactionDrafts.has(identity)) {
      const previousKey = `${state.sessionId}:question:${resumedAction}:reply`;
      if (interactionDrafts.has(previousKey))
        interactionDrafts.set(
          identity,
          structuredClone(interactionDrafts.get(previousKey)),
        );
    }
    if ($("#active-question").dataset.identity !== identity) {
      $("#active-question").replaceChildren(
        structuredUI.render(question, {
          ...summaryContext(false, state.reply.action_id),
          ...patientActionContext(),
          calculationDraft: calculationDrafts.get(question.data?.proposal_id),
          onCalculationDraft: (values) =>
            calculationDrafts.set(question.data.proposal_id, values),
          onConfirmCalculation: (proposalId, values) =>
            sendMessage(
              `核对换算：${values.value} ${values.from_unit} → ${values.to_unit}`,
              "confirm_calculation",
              { proposal_id: proposalId, input: values },
            ),
          onSkipCalculation: (proposalId) =>
            sendMessage("先跳过换算", "skip_calculation", {
              proposal_id: proposalId,
              input: {},
            }),
          onContinueCalculation: () =>
            sendMessage(
              state.reply.calculation_followup?.status === "pending"
                ? "继续处理补充"
                : "继续问诊",
              "resume_after_calculation",
              {
                execution_id: state.reply.calculation_result.execution_id,
              },
            ),
          questionDraft: interactionDrafts.get(identity),
          onQuestionDraft: (draft) => interactionDrafts.set(identity, draft),
        }),
      );
      const followup = structuredUI
        .blocks(replyEnvelope(state.reply))
        .find((block) => block.type === "interaction_followup");
      if (followup)
        $("#active-question").prepend(
          structuredUI.render(
            followup,
            summaryContext(false, state.reply.action_id),
          ),
        );
      if (prepared)
        $("#active-question").replaceChildren(
          structuredUI.render({
            id: `subject-draft:${state.sessionId}`,
            type: "subject_draft",
            data: prepared,
          }),
        );
      $("#active-question").dataset.identity = identity;
      $("#active-question").scrollTop = 0;
    }
  }
  document.body.classList.toggle(
    "question-focused",
    focusedQuestion && !state.taskHistory,
  );
  if (active) {
    const identity = `${state.sessionId}:${state.reply.action_id || task.id}`;
    if ($("#active-task").dataset.identity !== identity) {
      const block = structuredUI
        .blocks(replyEnvelope(state.reply))
        .find((item) => item.type === "self_check") || {
        id: "missing-task",
        type: "unsupported",
      };
      const draftKey = `${state.sessionId}:task:${task.interaction_id || state.reply.action_id}`;
      $("#active-task").replaceChildren(
        structuredUI.render(block, {
          actionId: state.reply.action_id,
          taskDraft: interactionDrafts.get(draftKey),
          onTaskDraft: (draft) => interactionDrafts.set(draftKey, draft),
          sessionId: state.sessionId,
          ...patientActionContext(),
          onAnswer: () => {
            if (!state.busy) $("#message-input").focus();
          },
        }),
      );
      $("#active-task").dataset.identity = identity;
      const pane = $("#active-task");
      pane.scrollTop = 0;
    }
  }
  $("#toggle-task-history").textContent = state.taskHistory
    ? active
      ? "返回当前任务"
      : summaryFocus
        ? "返回本次摘要"
        : "返回当前问题"
    : "展开对话记录";
  $("#toggle-task-history").setAttribute(
    "aria-expanded",
    String(state.taskHistory),
  );
  $("#quick-replies").classList.toggle(
    "visual-choices-available",
    focusedQuestion &&
      !state.taskHistory &&
      Boolean(question.data?.choices?.length),
  );
  if (!$("#toast").hidden) positionToast();
}
export function renderChoices(items) {
  if (preparedSubjectDraft()) items = [];
  $("#quick-replies").replaceChildren();
  items.forEach((item) =>
    $("#quick-replies").append(
      button(item.label, "", () => sendMessage(item.label, item.value)),
    ),
  );
  if (state.reply?.task?.id === "body_location" && !state.taskHistory) {
    $("#quick-replies")
      .querySelectorAll("button")
      .forEach((node, index) => {
        node.hidden = ["head", "chest", "abdomen", "back", "limbs"].includes(
          items[index]?.value,
        );
      });
  }
}
export function updateComposer() {
  const closed = state.case?.phase === "closed";
  $("#composer").hidden = Boolean(closed);
  $("#quick-replies").hidden = Boolean(closed);
  $(".composer-note").hidden = Boolean(closed);
  const placeholders = {
    age: "例如 8 个月，或 32 岁",
    chief_complaint: "描述最困扰你的问题…",
    onset: "例如：两天前，逐渐出现",
    severity: "例如：5 分，主要影响睡眠",
    medications_taken: "药名、剂量、时间，或写没有",
    allergies: "过敏物质与反应；没有 / 不知道请区分",
  };
  $("#message-input").placeholder = preparedSubjectDraft()
    ? "核对原话，可直接修改后发送…"
    : state.reply?.task?.id === "axillary_temperature"
      ? "填写测得的腋温，例如 38.2℃"
      : placeholders[state.reply?.question_id] ||
        "按自己的话说，也可以补充或更正…";
  $("#message-input").inputMode =
    state.reply?.task?.id === "axillary_temperature" ? "decimal" : "text";
  $("#composer-context").textContent = closed
    ? "本次会谈已结束。需要时可开始新的问诊。"
    : state.reply?.kind === "service_error" &&
        state.reply?.calculation_followup?.status === "pending"
      ? "补充原话已保留，可以重试。"
      : state.reply?.kind === "service_error" &&
          state.reply?.observation_result?.supplement?.status === "pending"
        ? "读数已保留，可以重试补充信息。"
        : state.reply?.kind === "subject_change"
          ? "可为另一位家人新建问诊，或继续当前问诊。"
          : state.reply?.kind === "calculation_input"
            ? "可修改上方输入，也可以先跳过；新的情况仍可直接告诉我。"
            : state.reply?.kind === "calculation_result"
              ? "核对结果后继续问诊，也可以补充新的情况。"
              : state.reply?.task
                ? "按图示完成后记录结果；做不到也可以跳过。"
                : state.case?.phase === "summary"
                  ? "请先核对摘要。你可以确认，也可以直接说需要更正的地方。"
                  : "可以按自己的话回答，也可以随时补充或更正。";
}
// P2c archive-reference banner: mounted at the top of the consultation
// view only while the session carries a non-empty baseline projection
// and the switch is on. Dismissing posts the switch off; the banner
// itself hides immediately and the server state (cleared projection)
// keeps it from remounting on the next render.
// S1 banner rule: at most one top banner is visible at a time, by
// priority connection-alert > retriage-banner > baseline banner. Losers
// are suppressed with a class (not the hidden attribute) so they return
// when the higher-priority banner clears.
export function enforceBannerPriority() {
  const connection = $("#connection-alert");
  const retriage = $("#retriage-banner");
  const baseline = $("#chengxin-baseline-banner");
  const connectionOn = Boolean(connection && !connection.hidden);
  const retriageOn = Boolean(retriage && !retriage.hidden);
  if (retriage) retriage.classList.toggle("banner-suppressed", connectionOn);
  if (baseline)
    baseline.classList.toggle("banner-suppressed", connectionOn || retriageOn);
}

export function syncBaselineBanner(data) {
  const existing = $("#chengxin-baseline-banner");
  const show = Boolean(
    data.profile_baseline &&
    Object.keys(data.profile_baseline).length &&
    data.baseline_reference_enabled,
  );
  if (!show) {
    existing?.remove();
    return;
  }
  if (existing) {
    enforceBannerPriority();
    return;
  }
  const banner = createBaselineBanner({
    onDismiss: async () => {
      try {
        const result = await api(
          `/api/sessions/${data.session_id}/baseline-reference`,
          { method: "POST", body: { enabled: false } },
        );
        if (result.state) renderCase(result.state);
      } catch (error) {
        banner.hidden = false;
        toast(error.message);
      }
    },
  });
  $("#view-consultation")?.prepend(banner);
  enforceBannerPriority();
}

runtime.navigateImpl = navigate;
