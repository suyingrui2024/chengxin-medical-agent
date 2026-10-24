import {
  createRenderer,
  replyEnvelope,
  calculationEnvelope,
} from "./structured-ui.js";
import { createExecutionUI } from "./execution-ui.js";
import {
  createBaselineBanner,
  createPatientRecordsUI,
} from "./patient-records-ui.js";
import { renderLocationChips } from "./location-answer-ui.js";
import {
  openHandoffPrintWindow,
  downloadHandoffFile,
} from "./handoff-print.js";

const $ = (selector) => document.querySelector(selector);
const taskProgress = new Map();
const calculationDrafts = new Map();
const composerDrafts = new Map();
const interactionDrafts = new Map();
const subjectDrafts = new Map();
const structuredUI = createRenderer({ taskProgress });
const names = {
  consult_for: "咨询对象",
  age_years: "年龄",
  chief_complaint: "主要问题",
  emergency_screen: "紧急信号筛查",
  onset: "开始时间",
  trajectory: "变化趋势",
  severity: "影响程度",
  associated_symptoms: "伴随不适",
  resp_breathing: "呼吸情况",
  resp_cough_details: "咳嗽情况",
  resp_intake: "饮水、进食与排尿",
  medications_taken: "用药与处理",
  allergies: "过敏信息",
  relevant_history: "相关病史",
  patient_goal: "期望与担心",
  temperature_c: "报告的腋温",
  symptom_location: "主观症状部位",
  unsolicited_note: "补充说明",
};
const values = {
  self: "本人",
  child: "孩子",
  other_adult: "其他家人",
  worsening: "加重",
  improving: "减轻",
  stable: "变化不大",
};
const sourceNames = {
  patient: "患者自述",
  caregiver: "照护者自述",
  device: "设备直连",
  camera: "相机观察",
  system_inference: "系统推断",
};
const qualityNames = {
  not_assessed: "质量未评价",
  adequate: "质量可用",
  inadequate: "质量不足",
  not_done: "未执行",
};
const modeNames = { demo: "离线演示", compatible: "直接模型", dsh: "DSH 问诊" };
const phaseNames = {
  opening: "明确问题",
  emergency_screen: "紧急信号",
  history: "了解经过",
  background: "补充背景",
  observation: "定向观察",
  summary: "待核对",
  closed: "已结束",
};
const phaseOrder = [
  "opening",
  "emergency_screen",
  "history",
  "background",
  "observation",
  "closed",
];
const state = {
  sessionId: null,
  case: null,
  reply: null,
  events: [],
  sessions: [],
  followUps: [],
  dueReminders: [],
  tasks: [],
  settings: { backend: "demo" },
  subagentMetrics: null,
  modelCredentials: { available: false, has_saved_config: false },
  view: "consultation",
  busy: false,
  starting: false,
  priorityBusy: false,
  messageEpoch: 0,
  pendingMessages: new Map(),
  filter: "all",
  editing: null,
  workspaceMode: "patient",
  catalog: null,
  capabilityBusy: false,
  taskHistory: false,
  lastCorrection: null,
  shownCalculations: new Map(),
  handoffText: "",
  handoff: null,
  reportDraft: null,
};
let pollTimer = null;
let toastTimer = null;
const executionUI = createExecutionUI(api);
const patientRecordsUI = createPatientRecordsUI(api, toast, {
  getConsultationContext: () => ({
    sessionId: state.sessionId,
    subject: state.case?.facts?.consult_for?.value || null,
    // P2d binding pointer: already reaches the frontend inside
    // state.case.runtime_snapshot (CaseState.to_dict), written only by
    // POST /api/sessions/{id}/bind-patient; no new field is invented.
    boundPatientId:
      state.case?.runtime_snapshot?.consultation_patient_id || null,
    reportItems: state.case?.report_items || [],
  }),
  openConsultation: () => navigate("consultation"),
  onAttached: (data) => {
    renderCase(data.state);
    navigate("consultation");
    toast(`已将 ${data.items.length} 项确认数据用于本次问诊。`);
  },
  onBound: (data) => {
    if (data?.state) renderCase(data.state);
  },
});

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function icon(name) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("class", "icon");
  node.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  node.append(use);
  return node;
}
function button(label, className, handler) {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
function positionToast() {
  const notice = $("#toast");
  const inline =
    window.matchMedia("(max-height: 560px)").matches &&
    !$("#view-consultation").hidden;
  const parent = inline
    ? document.querySelector(".conversation-footer")
    : document.body;
  notice.classList.toggle("toast-inline", inline);
  if (notice.parentElement !== parent) {
    if (inline) parent.insertBefore(notice, $("#composer"));
    else parent.append(notice);
  }
}
function toast(text) {
  clearTimeout(toastTimer);
  $("#toast").textContent = text;
  positionToast();
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 4500);
}
async function api(path, { method = "GET", body, signal } = {}) {
  const response = await fetch(path, {
    method,
    signal,
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    // A non-JSON body (proxy error page, truncated response) must not
    // leak the raw parser error ("Unexpected end of JSON input") to
    // the patient; report it as a retryable service problem instead.
    throw new Error(
      `服务返回了无法识别的响应（状态码 ${response.status}），请稍后重试`,
    );
  }
  if (!response.ok) {
    const error = new Error(payload.error?.message || "服务未能完成这一步");
    error.code = payload.error?.code;
    throw error;
  }
  return payload;
}
function factLabel(key) {
  const task = state.case?.runtime_snapshot?.composition?.capabilities.find(
    (item) => item.task?.result_key === key,
  )?.task;
  return names[key] || task?.result_label || task?.title || key;
}
function displayFact(key, fact) {
  if (fact.assertion === "unknown")
    return fact.quality === "not_done" ? "未执行 / 已跳过" : "未知 / 尚未确认";
  if (fact.assertion === "absent")
    return key === "emergency_screen" ? "用户否认上述紧急信号" : "明确否认";
  if (key === "emergency_screen") return "报告了紧急信号";
  if (typeof fact.value === "boolean") return fact.value ? "是" : "否";
  if (key === "age_years" && typeof fact.value === "number")
    return fact.value < 2
      ? `${Math.round(fact.value * 12)} 个月`
      : `${fact.value} 岁`;
  if (key === "temperature_c") {
    const attempt = state.case?.observation_attempts
      ?.filter((item) => item.task_id === "axillary_temperature")
      .at(-1);
    const pending =
      attempt?.previous_fact_id === fact.fact_id &&
      attempt.status !== "recorded";
    return `${fact.value} ℃${pending ? (attempt.finished_at ? " · 上次记录，本次未完成" : " · 上次记录，正在重测") : ""}`;
  }
  return (
    values[fact.value] ||
    (typeof fact.value === "object"
      ? JSON.stringify(fact.value)
      : String(fact.value))
  );
}
function formatTime(raw, date = false) {
  if (!raw) return "—";
  return new Intl.DateTimeFormat(
    "zh-CN",
    date
      ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
      : {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        },
  ).format(new Date(raw));
}
function setBusy(busy) {
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
const EDUCATION_POLL_LIMIT = 30;
let educationWatchKey = null;
let educationPollsLeft = 0;
function educationPending() {
  const conclusion = state.case?.conclusion;
  if (state.case?.phase !== "closed" || !conclusion) return false;
  return conclusion.further_reading?.status !== "ready";
}
function syncEventPolling() {
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
function scrollBottom() {
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
function focusCurrentAction() {
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
function navigate(view) {
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

function applyRetentionCopy() {
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

function setWorkspaceMode(mode) {
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

function appendMessage(text, role, kind = "") {
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
function renderTask(task, preview = false) {
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
function patientActionContext() {
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
function summaryContext(
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
function syncSummaryEdits() {
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
function renderReply(reply, { replay = false } = {}) {
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
function syncTaskFocus() {
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
function renderChoices(items) {
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
function updateComposer() {
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
function enforceBannerPriority() {
  const connection = $("#connection-alert");
  const retriage = $("#retriage-banner");
  const baseline = $("#chengxin-baseline-banner");
  const connectionOn = Boolean(connection && !connection.hidden);
  const retriageOn = Boolean(retriage && !retriage.hidden);
  if (retriage) retriage.classList.toggle("banner-suppressed", connectionOn);
  if (baseline)
    baseline.classList.toggle("banner-suppressed", connectionOn || retriageOn);
}

function syncBaselineBanner(data) {
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

function renderCase(data) {
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
function emptyState(message) {
  const node = el("div", "empty-state compact");
  node.append(icon("cards"), el("p", "", message));
  return node;
}
function renderReportItems(items) {
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
function reportItemText(item) {
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
function renderReportDraft(draft) {
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
async function openReportDraft() {
  if (!state.sessionId) return;
  $("#report-feedback").textContent = "";
  $("#report-dialog").showModal();
  $("#report-text").focus();
}
async function submitReportDraft(event) {
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
async function confirmReportItem(draftId, sourceItemId) {
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
async function removeReportItem(itemId) {
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
function handoffValue(value) {
  return typeof value === "object"
    ? JSON.stringify(value)
    : String(value ?? "");
}
function handoffLines(handoff) {
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
function renderHandoff(handoff, renderDialog = true) {
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
async function openHandoff() {
  if (!state.sessionId) return;
  try {
    const data = await api(`/api/sessions/${state.sessionId}/handoff`);
    // P4-D mapping (handoff.py itself is untouched): the v2 endpoint
    // carries directions only when visible and no narrative at all,
    // while handoff-print.js reads directions_visible + narrative.
    // Derive directions_visible from the live sheet when available
    // (falling back to "directions present"), and take the narrative
    // from the live sheet, which the endpoint does not carry. The
    // dialog rendering above still uses the raw endpoint object.
    const sheet = state.case?.conclusion || null;
    const rawConclusion = data.handoff?.conclusion || null;
    // M2 纵深防御：narrative.reasoning 逐条含方向标签，shadow 下不
    // 抄进 state.handoff（打印模块另有同门控，双重保险）；framing /
    // severity 不含方向，可保留。
    const visible = sheet
      ? Boolean(sheet.directions_visible)
      : rawConclusion?.directions != null;
    const narrative = sheet?.narrative
      ? {
          framing: sheet.narrative.framing,
          severity: sheet.narrative.severity,
          ...(visible ? { reasoning: sheet.narrative.reasoning } : {}),
        }
      : undefined;
    state.handoff = data.handoff
      ? {
          ...data.handoff,
          conclusion: rawConclusion
            ? {
                ...rawConclusion,
                directions_visible: visible,
                narrative,
              }
            : null,
        }
      : null;
    renderHandoff(data.handoff);
    $("#handoff-dialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}
async function copyHandoff() {
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
function renderConversationSubtitle(backend = state.settings?.backend) {
  const subject = state.case?.consultation_subject;
  const prepared = preparedSubjectDraft();
  const actualBackend =
    state.case?.runtime_snapshot?.transport?.adapter ||
    state.sessions.find((item) => item.session_id === state.sessionId)
      ?.backend ||
    backend;
  const mode = modeNames[actualBackend] || "问诊";
}

function preparedSubjectDraft() {
  return state.reply?.kind === "question" &&
    state.case &&
    !Object.keys(state.case.facts).length
    ? subjectDrafts.get(state.sessionId)
    : null;
}
function rememberComposerDraft() {
  if (state.sessionId)
    composerDrafts.set(state.sessionId, $("#message-input").value);
}
function restoreComposerDraft(id) {
  $("#message-input").value = composerDrafts.get(id) || "";
  $("#message-input").dispatchEvent(new Event("input"));
}

async function startSession() {
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
async function sendMessage(
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

function syncPatientBusy() {
  const pending = [...state.pendingMessages.values()].includes(state.sessionId);
  setBusy(state.starting || (pending && state.case?.phase !== "closed"));
}

function applyPatientResponse(data, sessionId) {
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

async function requestEmergencyHelp(event) {
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
async function resumeSession(id, force = false) {
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
let sessionsGeneration = 0;
async function refreshSessions() {
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
function renderRecent() {
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
function renderHistory() {
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
const FOLLOW_UP_KIND_LABELS = {
  urgent_checkin: "紧急确认",
  routine_checkin: "例行回访",
};
let followUpTarget = null;
let followUpRetriageId = null;
async function refreshFollowUps() {
  try {
    const data = await api("/api/follow-ups");
    state.followUps = data.follow_ups || [];
  } catch {
    state.followUps = [];
  }
  try {
    // Medication reminders (P4-B) share the due center; a failed fetch
    // simply means "no reminders", never an error surface.
    const data = await api("/api/medication-reminders");
    state.dueReminders = Array.isArray(data.reminders)
      ? data.reminders
      : Array.isArray(data)
        ? data
        : [];
  } catch {
    state.dueReminders = [];
  }
  renderFollowUps();
}
// Due rule shared with desktop/due-notifications.cjs (kept in sync by
// hand; the desktop module is canonical). A reminder is due when it is
// not completed and either its timestamped due time <= now, or — for
// P4-B plan entries — today's local occurrence of a scheduled clock time
// has passed. Returns distinct medication names. Names only — no dose
// field is ever read here.
function dueReminderNames() {
  const now = new Date();
  const names = new Set();
  for (const item of state.dueReminders || []) {
    if (!item || typeof item !== "object") continue;
    const name = item.medication_name || item.name;
    if (!name) continue;
    const completed =
      item.status === "completed" ||
      item.status === "done" ||
      item.done === true ||
      item.outcome != null;
    if (completed) continue;
    if (Array.isArray(item.scheduled_times)) {
      if (item.status !== "scheduled") continue;
      for (const clock of item.scheduled_times) {
        const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(
          typeof clock === "string" ? clock.trim() : "",
        );
        if (!match) continue;
        const occurrence = new Date(
          now.getFullYear(),
          now.getMonth(),
          now.getDate(),
          Number(match[1]),
          Number(match[2]),
        );
        if (occurrence <= now) names.add(name);
      }
      continue;
    }
    const dueAt = Date.parse(
      item.due_at || item.remind_at || item.scheduled_at || "",
    );
    if (Number.isFinite(dueAt) && dueAt <= now.getTime()) names.add(name);
  }
  return [...names];
}
function dueReminderCount() {
  return dueReminderNames().length;
}
async function refreshRecordsCount() {
  // The badge in the nav is static 0 in the HTML and the records module
  // only corrects it when the records view is opened; pull just the
  // timeline up front so the first screen shows the real count.
  const data = await api("/api/patient/timeline");
  $("#records-count").textContent = String((data.entries || []).length);
}
function followUpDueCount() {
  const now = Date.now();
  return (state.followUps || []).filter((item) => {
    if (!item.plan || item.plan.status !== "scheduled") return false;
    const dueAt = Date.parse(item.plan.due_at);
    return Number.isFinite(dueAt) && dueAt <= now;
  }).length;
}
function renderFollowUpBadge() {
  const badge = $("#followup-due-count");
  // Due center: one badge aggregates due follow-ups AND due medication
  // reminders, counted by the same due rule the desktop checker uses.
  const due = followUpDueCount();
  const dueMeds = dueReminderCount();
  const total = due + dueMeds;
  if (badge) {
    badge.hidden = total === 0;
    badge.textContent = String(total);
    badge.title = [
      due ? `${due} 个随访已到回访时间` : "",
      dueMeds ? `${dueMeds} 个用药提醒已到时间` : "",
    ]
      .filter(Boolean)
      .join("；");
  }
  // One web notification per page load when follow-ups are due —
  // the follow-up window (24–72h) is exactly when patients forget.
  if (
    due > 0 &&
    "Notification" in window &&
    Notification.permission === "granted" &&
    !window.__followUpNotified
  ) {
    window.__followUpNotified = true;
    try {
      new Notification("有随访到期", {
        body: `${due} 个随访计划已到回访时间，打开「会谈记录」记录近况。`,
      });
    } catch {
      /* notifications are best-effort */
    }
  }
  // Medication reminders reuse the same per-load web channel; the body
  // names medications only and never carries a dose.
  if (
    dueMeds > 0 &&
    "Notification" in window &&
    Notification.permission === "granted" &&
    !window.__medicationNotified
  ) {
    window.__medicationNotified = true;
    try {
      const names = dueReminderNames();
      const shown = names.slice(0, 3).join("、");
      const rest = names.length > 3 ? `等 ${names.length} 种` : "";
      new Notification("该吃药了", { body: `该吃${shown}${rest}了。` });
    } catch {
      /* notifications are best-effort */
    }
  }
}
// Desktop notification click-through: the main process focuses the window
// and routes here; the due center lives in the history view (follow-up
// section + badge), for follow-ups and medication reminders alike.
if (window.chengxinDesktop?.dueNotifications?.onOpen) {
  window.chengxinDesktop.dueNotifications.onOpen(() => {
    navigate("history");
    document
      .getElementById("followup-section")
      ?.scrollIntoView({ block: "start" });
  });
}
function renderFollowUps() {
  const section = $("#followup-section");
  const list = $("#followup-list");
  if (!section || !list) return;
  // Boundary, stated where the due items live: desktop notifications
  // only arrive while the app is open. Closing the app never pushes;
  // nothing is lost — due items stay aggregated here and in the badge.
  // (No background service, no resident process, by design — D4.)
  if (!$("#followup-boundary-note")) {
    const note = el(
      "p",
      "followup-hint",
      "桌面通知仅在应用打开时送达：关闭应用后不会推送，到期项仍会在这里和顶部徽标中汇总。",
    );
    note.id = "followup-boundary-note";
    section.insertBefore(note, list);
  }
  const pending = (state.followUps || []).filter(
    (item) => item.plan && item.plan.status === "scheduled",
  );
  renderFollowUpBadge();
  list.replaceChildren();
  $("#medication-reminder-schedule")?.remove();
  const reminders = state.dueReminders || [];
  section.hidden = pending.length === 0 && reminders.length === 0;
  const now = Date.now();
  for (const item of pending) {
    const dueAt = Date.parse(item.plan.due_at);
    const due = Number.isFinite(dueAt) && dueAt <= now;
    const card = el("article", "followup-card");
    const info = el("div", "history-info");
    info.append(
      el("strong", "", item.title || "上次问诊"),
      el(
        "small",
        "",
        `${FOLLOW_UP_KIND_LABELS[item.plan.kind] || "随访"} · ${item.plan.reason || ""}`,
      ),
      el(
        "small",
        "",
        `约定回访时间 ${formatTime(item.plan.due_at, true)}${due ? " · 已到期" : ""}`,
      ),
    );
    const badge = el(
      "span",
      due ? "followup-badge due" : "followup-badge",
      due ? "已到期" : "待随访",
    );
    const action = button("记录近况", "primary-button", () =>
      openFollowUpDialog(item),
    );
    card.append(info, badge, action);
    // A drafted follow-up message (workspace.follow_up_drafts, filled
    // by the offline batch) is shown read-only for confirmation; it is
    // never sent from here.
    const draftText =
      typeof item.draft === "string"
        ? item.draft
        : item.draft?.draft_message || item.draft?.text || "";
    if (draftText) {
      const draft = el("div", "followup-draft");
      draft.append(
        el("p", "followup-draft-label", "草稿，待确认"),
        el("p", "followup-draft-text", draftText),
      );
      card.append(draft);
    }
    list.append(card);
  }
  // P4 medication-reminder scheduling (minimal): each reminder shows
  // its name and a comma-separated HH:MM input; saving posts only the
  // name and HH:MM times — never a dose — to the schedule route, then
  // reloads. Multi-time input is deliberate: a single-time box used
  // to silently drop a second saved time on the next save (N2).
  if (reminders.length) {
    // S3: the schedule no longer sits as a standalone block after the
    // follow-up list; it lives inside the list as one collapsed item.
    const block = el("div", "medication-reminder-schedule");
    block.id = "medication-reminder-schedule";
    const medToggle = el(
      "button",
      "conclusion-collapse-toggle",
      `用药提醒时间 · ${reminders.length} 种药物`,
    );
    medToggle.type = "button";
    medToggle.setAttribute("aria-expanded", "false");
    const medBody = el("div", "conclusion-collapse-body");
    medBody.hidden = true;
    medToggle.addEventListener("click", () => {
      medBody.hidden = !medBody.hidden;
      medToggle.setAttribute(
        "aria-expanded",
        medBody.hidden ? "false" : "true",
      );
    });
    block.append(medToggle, medBody);
    medBody.append(
      el(
        "p",
        "followup-hint",
        "只提醒“该吃药了”，不涉及剂量；多个时间用逗号分隔（如 08:00，20:00），未设定时间的药物显示“待设定”。",
      ),
    );
    for (const item of reminders) {
      const name = item.medication_name || item.name || "";
      if (!name) continue;
      const row = el("div", "medication-reminder-row");
      const label = el(
        "span",
        "",
        item.purpose ? `${name}（${item.purpose}）` : name,
      );
      const input = el("input");
      input.type = "text";
      input.placeholder = "如 08:00，20:00";
      input.value = Array.isArray(item.scheduled_times)
        ? item.scheduled_times.join("，")
        : "";
      input.setAttribute("aria-label", `${name}提醒时间`);
      const feedback = el("span", "followup-hint", "");
      const save = button("保存时间", "secondary-button", async () => {
        const raw = input.value
          .split(/[,，、]/)
          .map((part) => part.trim())
          .filter(Boolean);
        const valid = raw.every(/^([01]\d|2[0-3]):[0-5]\d$/.test);
        if (!valid) {
          feedback.textContent =
            "时间格式不对：请用 24 小时制的 HH:MM，多个时间用逗号分隔。";
          return;
        }
        feedback.textContent = "";
        try {
          await api("/api/medication-reminders/schedule", {
            method: "POST",
            body: { medication_name: name, times: raw },
          });
          toast(`已保存${name}的提醒时间。`);
          await refreshFollowUps();
        } catch (error) {
          feedback.textContent = error.message;
        }
      });
      row.append(label, input, save, feedback);
      medBody.append(row);
    }
    list.append(block);
  }
}
function openFollowUpDialog(item) {
  followUpTarget = item;
  followUpRetriageId = null;
  $("#followup-context").textContent =
    `${item.title || "上次问诊"} · ${FOLLOW_UP_KIND_LABELS[item.plan.kind] || "随访"}（约定 ${formatTime(item.plan.due_at, true)}）`;
  $("#followup-form").reset();
  $("#followup-feedback").textContent = "";
  $("#followup-retriage").hidden = true;
  $("#followup-dialog").showModal();
}
function renderLibrary() {
  $("#task-library").replaceChildren();
  if (!state.tasks.length)
    $("#task-library").append(
      emptyState(
        "当前新问诊模板未启用图示任务。可在“能力与模板”中调整；旧会谈仍保留原任务。",
      ),
    );
  for (const task of state.tasks) {
    const card = el("article", "library-card");
    const image = el("img", "library-image");
    // 问诊任务用同一 SVG 的 #step-N 视图片段；本会话一旦加载过片段视图，
    // 无片段加载会继承会话内的活动视图状态，把整幅画布缩在中央（F11a 根因）。
    // 库预览改用第一步的显式视图（与任务内渲染一致、幅面清晰），无分步图才用原图。
    image.src = task.step_assets?.[0] || task.asset;
    image.alt = `${task.title}图示预览`;
    // 图资产是无固有像素尺寸的 SVG：加载后按真实宽高比锁定画框，
    // 避免个别引擎把内容缩在画框中央（走查 F11a）。
    image.addEventListener("load", () => {
      if (image.naturalWidth > 0 && image.naturalHeight > 0) {
        image.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`;
        image.style.height = "auto";
        image.style.maxHeight = "240px";
      }
    });
    const heading = el("div", "library-heading");
    heading.append(
      el("h2", "", task.title),
      el("span", "review-chip", "待临床审核"),
    );
    const meta = el("div", "library-meta");
    meta.append(
      el("span", "", `${task.steps.length} 个步骤 · 可跳过`),
      el("span", "", task.equipment.length ? "需要指定设备" : "无需设备"),
    );
    card.append(
      image,
      heading,
      el("p", "", task.purpose),
      meta,
      button("查看图示与步骤", "secondary-button", () => {
        $("#task-preview").replaceChildren(renderTask(task, true));
        $("#task-dialog").showModal();
      }),
    );
    $("#task-library").append(card);
  }
}

function refreshConclusionCard(conclusion) {
  // Education writes further_reading back onto the stored conclusion
  // after the closing frame rendered the card, so the card sitting in
  // the transcript is frozen with the pre-enrichment sheet. Re-render
  // the existing card node from the current conclusion; sessions whose
  // transcript has no conclusion card are left untouched.
  if (!conclusion) return;
  const cards = $("#messages").querySelectorAll(
    '[data-block-type="conclusion"]',
  );
  if (!cards.length) return;
  const latest = state.reply;
  const block = (latest &&
    structuredUI
      .blocks(replyEnvelope(latest))
      .find((item) => item.type === "conclusion")) || {
    id: "conclusion:refresh",
    type: "conclusion",
  };
  cards[cards.length - 1].replaceWith(
    structuredUI.render(
      { ...block, data: conclusion },
      summaryContext(
        true,
        latest?.action_id,
        latest?.location_revision_action,
        null,
        true,
      ),
    ),
  );
  // The replacement node needs its focused-outcome marking re-applied.
  syncTaskFocus();
}

async function loadEvents() {
  const id = state.sessionId;
  if (!id) return;
  const data = await api(`/api/sessions/${id}/events`);
  if (state.sessionId !== id) return;
  if (data.events.length < state.events.length) return;
  const added = data.events.slice(state.events.length);
  state.events = data.events;
  const educationUpdated = added.some(
    (event) =>
      event.event_type === "web.citation_attached" ||
      event.event_type === "web.education_completed",
  );
  if (
    added.some(
      (event) => event.event_type.startsWith("advisor.") || educationUpdated,
    )
  ) {
    const snapshot = await api(`/api/sessions/${id}/snapshot`);
    if (state.sessionId === id) {
      renderCase(snapshot.state);
      if (educationUpdated) refreshConclusionCard(snapshot.state.conclusion);
    }
  }
  renderInspection();
}

function renderCalculation(result) {
  const node = el("div", "calculation-result");
  node.append(structuredUI.renderEnvelope(calculationEnvelope(result)));
  return node;
}
function appendCalculation(result) {
  const key = result.execution_id || JSON.stringify(result.input);
  const signature = JSON.stringify(calculationEnvelope(result));
  const previous = state.shownCalculations.get(key);
  if (previous?.signature === signature && previous.node.isConnected) return;
  const node = renderCalculation(result);
  if (previous?.node.isConnected) previous.node.replaceWith(node);
  else $("#messages").append(node);
  state.shownCalculations.set(key, { signature, node });
}
async function refreshCapabilities() {
  const [catalog, tasks] = await Promise.all([
    api("/api/capabilities"),
    api("/api/tasks"),
  ]);
  state.catalog = catalog;
  state.tasks = tasks.tasks;
  renderCapabilities();
  renderLibrary();
}
function templateDescription() {
  const template = state.catalog.templates.find(
    (item) => item.id === $("#template-select").value,
  );
  $("#template-description").textContent = template?.description || "";
}
function renderCapabilities() {
  const catalog = state.catalog;
  if (!catalog) return;
  $("#template-select").replaceChildren(
    ...catalog.templates.map((template) => {
      const option = el(
        "option",
        "",
        `${template.title} · ${template.version}`,
      );
      option.value = template.id;
      return option;
    }),
  );
  $("#template-select").value = catalog.template.id;
  templateDescription();
  const current = state.case?.runtime_snapshot?.composition;
  $("#composition-note").textContent =
    `新会谈配置 ${catalog.revision.slice(0, 12)}${current ? `；当前会谈固定为 ${current.sha256.slice(0, 12)}${current.sha256 !== catalog.revision ? "（与新配置不同）" : ""}` : "；尚未选择会谈"}。变更不会修改旧病例。`;
  $("#capability-count").textContent =
    `${catalog.capabilities.filter((item) => item.enabled).length} / ${catalog.capabilities.length} 已启用`;
  $("#capability-list").replaceChildren();
  $("#capability-select").replaceChildren();
  for (const item of catalog.capabilities) {
    const card = el("article", "capability-card");
    card.dataset.capabilityId = item.id;
    const heading = el("div", "surface-heading");
    const identity = el("div");
    identity.append(
      el("h3", "", item.name),
      el("p", "capability-id", `${item.id} · ${item.version}`),
    );
    const toggle = button(
      item.enabled ? "停用" : "启用",
      "secondary-button capability-toggle",
      () => changeComposition({ id: item.id, enabled: !item.enabled }),
    );
    toggle.setAttribute(
      "aria-label",
      `${item.enabled ? "停用" : "启用"} ${item.name}`,
    );
    heading.append(identity, toggle);
    card.append(
      heading,
      el("p", "", item.description),
      el(
        "p",
        "field-hint",
        `${item.kind === "self_check" ? "图示内容包 · 待临床审核" : "执行插件 · 工程验证，非临床评价"} · 外部权限：无`,
      ),
      el("p", "field-hint", item.validation?.scope || "未提供验证说明"),
    );
    const details = el("details", "manifest-details");
    details.append(
      el("summary", "", "查看契约与内容清单"),
      el("pre", "", JSON.stringify(item, null, 2)),
    );
    card.append(details);
    $("#capability-list").append(card);
    if (item.enabled && item.kind === "calculation") {
      const option = el("option", "", item.name);
      option.value = item.id;
      $("#capability-select").append(option);
    }
  }
  renderCapabilityInputs();
}
function renderCapabilityInputs() {
  const item = state.catalog?.capabilities.find(
    (item) => item.id === $("#capability-select").value,
  );
  $("#capability-inputs").replaceChildren();
  $("#capability-test-result").replaceChildren();
  $("#capability-test-feedback").textContent = "";
  $("#run-capability-test").disabled = !item;
  if (!item) {
    $("#capability-inputs").append(emptyState("当前模板没有启用计算能力。"));
    return;
  }
  let index = 0;
  for (const [key, field] of Object.entries(item.input_schema.properties)) {
    const id = `capability-input-${index++}`;
    const label = el("label", "field-label", field.title || key);
    label.htmlFor = id;
    const input = el(field.enum ? "select" : "input");
    if (field.enum)
      field.enum.forEach((value) => {
        const option = el("option", "", value);
        option.value = String(value);
        input.append(option);
      });
    else input.type = "text";
    input.id = id;
    input.name = key;
    input.required = item.input_schema.required.includes(key);
    input.value = String(item.example?.[key] ?? "");
    $("#capability-inputs").append(label, input);
  }
}
async function changeComposition(change) {
  if (state.capabilityBusy || state.busy) {
    toast("请等待当前操作完成。");
    return;
  }
  setCapabilityBusy(true);
  $("#capability-feedback").textContent = "正在校验组合…";
  try {
    await api("/api/capabilities/configure", {
      method: "POST",
      body: { ...change, expected_revision: state.catalog.revision },
    });
    await refreshCapabilities();
    $("#capability-feedback").textContent =
      "已应用到新会谈；当前会谈与历史结果保持原版本。";
  } catch (error) {
    $("#capability-feedback").textContent = error.message;
    if (error.code === "stale_composition") await refreshCapabilities();
  } finally {
    setCapabilityBusy(false);
  }
}
function setCapabilityBusy(busy) {
  state.capabilityBusy = busy;
  $("#view-capabilities")
    .querySelectorAll("button,input,select")
    .forEach((node) => {
      node.disabled = busy;
    });
  if (!busy)
    $("#run-capability-test").disabled = !$("#capability-select").value;
}
async function previewCapability(event) {
  event.preventDefault();
  if (state.capabilityBusy || !state.catalog) return;
  const item = state.catalog.capabilities.find(
    (item) => item.id === $("#capability-select").value,
  );
  if (!item) return;
  const input = {};
  const feedback = $("#capability-test-feedback");
  $("#capability-test-result").replaceChildren();
  try {
    for (const [key, value] of new FormData($("#capability-test-form"))) {
      const kind = item.input_schema.properties[key].type;
      input[key] = kind === "string" ? value : JSON.parse(value);
    }
    setCapabilityBusy(true);
    feedback.textContent = "正在执行并校验结果…";
    const result = await api("/api/capabilities/preview", {
      method: "POST",
      body: { id: item.id, input, expected_revision: state.catalog.revision },
    });
    $("#capability-test-result").append(renderCalculation(result));
    feedback.textContent = "执行完成，未读取或写入病例。";
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    setCapabilityBusy(false);
  }
}
const eventLabels = {
  "session.created": "创建会谈",
  "agent.started": "启动问诊协调器",
  "turn.user_message": "收到用户描述",
  "turn.agent_reply": "输出患者可见回复",
  "fact.recorded": "记录病例事实",
  "fact.invalidated": "使依赖事实失效",
  "fact.correction_requested": "提交信息更正",
  "fact.restored": "撤销信息更正",
  "triage.assessed": "重新评估风险",
  "tool.requested": "请求医疗工具",
  "tool.completed": "工具执行完成",
  "model.requested": "提交模型请求",
  "model.responded": "收到模型响应",
  "model.failed": "模型调用失败",
  "agent.backend_failed": "决策服务失败",
  "advisor.queued": "安排后台复核",
  "advisor.skipped": "跳过无变化的后台复核",
  "advisor.started": "开始后台复核",
  "advisor.completed": "完成后台复核",
  "advisor.cancelled": "取消过期复核",
  "advisor.discarded": "丢弃过期复核结果",
  "advisor.unavailable": "后台复核暂不可用",
  "web.search_requested": "发起网络检索",
  "web.search_completed": "网络检索完成",
  "web.fetch_completed": "网页抓取完成",
  "web.blocked": "拦截不合规的联网请求",
  "web.citation_attached": "延伸阅读已补充",
  "web.education_completed": "延伸阅读生成完成",
  "web.education_failed": "延伸阅读生成失败",
  "web.conflict_detected": "网络资料与安全提示冲突（已拦截）",
  "self_check.offered": "展示图示任务",
  "self_check.completed": "记录任务反馈",
  "turn.rejected": "拒绝过期回答",
  "capability.requested": "请求能力执行",
  "capability.completed": "保存计算草稿（输入待核对）",
  "capability.failed": "能力执行失败，未写入结果",
};
function eventTitle(event) {
  const p = event.payload;
  if (event.event_type === "fact.recorded")
    return `记录 · ${names[p.key] || p.key}`;
  if (event.event_type === "tool.requested") return `调用 · ${p.tool}`;
  if (event.event_type === "tool.completed")
    return p.result?.ok === false ? `拒绝 · ${p.tool}` : `完成 · ${p.tool}`;
  return eventLabels[event.event_type] || event.event_type;
}
function visiblePayload(value) {
  if (Array.isArray(value)) return value.map(visiblePayload);
  if (value && typeof value === "object") {
    if (["reasoning", "thinking", "redacted_thinking"].includes(value.type))
      return { type: value.type, content: "推理内容未展示" };
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        ["reasoning_content", "thinking", "chain_of_thought"].includes(key)
          ? "推理内容未展示"
          : visiblePayload(item),
      ]),
    );
  }
  return value;
}
function renderInspection() {
  const has = Boolean(state.sessionId);
  $("#inspection-empty").hidden = has;
  $("#inspection-content").hidden = !has;
  $("#export-session").disabled = !has || state.busy;
  if (!has) return;
  const events = state.events;
  const facts = state.case?.facts || {};
  const unknown = Object.values(facts).filter(
    (f) => f.assertion === "unknown",
  ).length;
  const requested = events.filter(
    (e) => e.event_type === "tool.requested",
  ).length;
  const rejected = events.filter(
    (e) => e.event_type === "tool.completed" && e.payload.result?.ok === false,
  ).length;
  const reviews = events.filter((e) => e.event_type.startsWith("advisor."));
  const completedReviews = reviews.filter(
    (e) => e.event_type === "advisor.completed",
  );
  const reviewDuration = completedReviews.reduce(
    (total, event) => total + (event.payload.duration_ms || 0),
    0,
  );
  const metrics = [
    [
      "已记录信息",
      Object.keys(facts).length,
      `${unknown} 项未知，保留原始状态`,
    ],
    ["医疗工具调用", requested, `${rejected} 次提议被校验拒绝`],
    [
      "模型请求",
      events.filter((e) => e.event_type === "model.requested").length,
      "直接读取真实请求事件",
    ],
    [
      "后台复核",
      completedReviews.length,
      completedReviews.length
        ? `完成 ${completedReviews.length} 次 · 平均 ${Math.round(reviewDuration / completedReviews.length)} ms`
        : reviews.length
          ? `已记录 ${reviews.length} 个复核事件，尚未完成`
          : "当前会谈尚未触发复核",
    ],
    [
      "会谈状态",
      phaseNames[state.case?.phase] || "—",
      state.case?.risk?.level === "undetermined"
        ? "紧急程度尚未确定"
        : state.case?.risk?.level === "emergency"
          ? "已进入立即求助路径"
          : state.case?.risk?.level === "urgent"
            ? "已进入尽快评估路径"
            : "按当前配置记录，非诊断结论",
    ],
  ];
  $("#inspect-metrics").replaceChildren();
  for (const [label, value, note] of metrics) {
    const metric = el("div", "metric");
    metric.append(
      el("div", "metric-label", label),
      el("div", "metric-value", value),
      el("div", "metric-note", note),
    );
    $("#inspect-metrics").append(metric);
  }
  const stages = [
    ["接收", "turn.user_message", "用户输入"],
    ["决策", "model.requested", "模型请求"],
    ["校验执行", "tool.completed", "医疗工具"],
    ["回复", "turn.agent_reply", "可见动作"],
  ];
  $("#execution-flow").replaceChildren();
  for (const [name, type, note] of stages) {
    const count = events.filter((e) => e.event_type === type).length;
    const node = el("div", `flow-step ${count ? "observed" : ""}`);
    node.append(el("strong", "", name), el("span", "", `${note} · ${count}`));
    $("#execution-flow").append(node);
  }
  const opened = new Set(
    [...$("#event-list").querySelectorAll("details[open]")].map(
      (node) => node.dataset.eventId,
    ),
  );
  $("#event-list").replaceChildren();
  const selected = events.filter(
    (e) =>
      state.filter === "all" || e.event_type.startsWith(`${state.filter}.`),
  );
  if (!selected.length)
    $("#event-list").append(
      emptyState(
        state.filter === "tool" && state.settings.backend === "demo"
          ? "离线演示不执行模型工具循环。切换模型后端后可检查真实工具事件。"
          : "当前筛选下尚无事件。",
      ),
    );
  for (const event of [...selected].reverse()) {
    const failed =
      event.event_type.includes("failed") || event.payload.result?.ok === false;
    const row = el("details", `event-row ${failed ? "failed" : ""}`);
    row.dataset.eventId = event.event_id;
    row.open = opened.has(event.event_id);
    const summary = el("summary");
    const main = el("div", "event-title", eventTitle(event));
    main.append(el("div", "event-type", event.event_type));
    summary.append(
      main,
      el("time", "event-time", formatTime(event.occurred_at)),
    );
    row.append(
      summary,
      el("pre", "", JSON.stringify(visiblePayload(event.payload), null, 2)),
    );
    $("#event-list").append(row);
  }
  $("#evidence-count").textContent = `${Object.keys(facts).length} 项`;
  $("#evidence-list").replaceChildren();
  if (!Object.keys(facts).length)
    $("#evidence-list").append(emptyState("还没有写入病例事实。"));
  for (const [key, fact] of Object.entries(facts)) {
    const card = el("article", "evidence-item");
    const top = el("div", "evidence-top");
    top.append(
      el("span", "", factLabel(key)),
      el(
        "span",
        "",
        fact.assertion === "unknown"
          ? "未知"
          : fact.assertion === "absent"
            ? "明确否认"
            : "已提供",
      ),
    );
    card.append(top, el("p", "evidence-value", displayFact(key, fact)));
    if (fact.evidence)
      card.append(el("blockquote", "evidence-quote", `“${fact.evidence}”`));
    else
      card.append(
        el(
          "p",
          "evidence-meta",
          "本条未提供逐字证据片段，可在事件中核对原始输入。",
        ),
      );
    card.append(
      el(
        "div",
        "evidence-meta",
        `${sourceNames[fact.source] || fact.source} · ${qualityNames[fact.quality] || fact.quality}`,
      ),
    );
    if (fact.supersedes)
      card.append(el("div", "evidence-meta", "已更正 · 历史事实仍保留"));
    $("#evidence-list").append(card);
  }
}
function openEdit(key, fact) {
  if (state.busy) return;
  state.editing = { key, factId: fact.fact_id, sessionId: state.sessionId };
  $("#edit-label").textContent = `更正「${names[key]}」`;
  $("#edit-value").value =
    fact.assertion === "present"
      ? values[fact.value] || String(fact.value)
      : fact.assertion === "unknown"
        ? fact.evidence || ""
        : "";
  $("#edit-feedback").textContent = "";
  $("#edit-new-session").hidden = key !== "consult_for";
  $("#edit-value").hidden = key === "consult_for";
  $("#edit-value").required = key !== "consult_for";
  $("#edit-form button[type=submit]").hidden = key === "consult_for";
  $("#edit-hint").textContent =
    key === "consult_for"
      ? "本次咨询对象已固定。更换对象请另开一次问诊，当前记录会保留。"
      : key === "chief_complaint"
        ? "更改主要问题后，会重新确认本次症状经过、处理和紧急信号；旧记录仍保留在历史中。"
        : "保留更正前的记录，并重新评估受影响的信息。同一患者更正年龄会保留已报告的体温。";
  $("#edit-dialog").showModal();
  (key === "consult_for" ? $("#edit-new-session") : $("#edit-value")).focus();
}
async function restoreCorrection(correction) {
  if (
    state.busy ||
    state.sessionId !== correction.sessionId ||
    state.case?.phase !== "summary" ||
    state.case?.facts[correction.key]?.fact_id !== correction.factId
  )
    return;
  setBusy(true);
  try {
    const data = await api(
      `/api/sessions/${correction.sessionId}/facts/${correction.key}/restore`,
      { method: "PATCH", body: { expected_fact_id: correction.factId } },
    );
    appendMessage(`撤销更正${names[correction.key]}。`, "user");
    renderCase(data.state);
    state.lastCorrection = null;
    renderReply(data.reply);
    toast(`已撤销“${names[correction.key]}”的本次更正。`);
    await Promise.all([loadEvents(), refreshSessions()]);
  } catch (error) {
    toast(`无法撤销本次更正：${error.message}`);
    if (error.code === "stale_fact")
      await resumeSession(correction.sessionId, true).catch(() => {});
  } finally {
    setBusy(false);
  }
}
function renderModeFields() {
  const mode = $("#settings-form").elements.backend.value;
  $("#model-fields").hidden = mode === "demo";
  $("#model-fields")
    .querySelectorAll("input")
    .forEach((input) => {
      input.disabled = mode === "demo";
    });
  $("#protocol-hint").textContent =
    mode === "dsh"
      ? "DSH 使用 DeepSeek Messages 协议，例如 https://api.deepseek.com/anthropic。"
      : "填写 Chat Completions 的根地址，例如 https://你的服务/v1。";
  const reviewNotice = $("#dsh-review-notice");
  const reviewControl = $("#dsh-review-control");
  reviewNotice.hidden = mode !== "dsh";
  reviewControl.hidden = mode !== "dsh";
  if (mode === "dsh")
    reviewNotice.textContent =
      state.settings?.dsh_review_mode === "advisory"
        ? "DSH 会收到已提交内容的结构化病例快照进行受限复核；仅作为下一轮追问的内部参考，不会直接形成患者结论。"
        : "DSH 会收到已提交内容的结构化病例快照，用于后台影子复核；它不会读取正在输入的内容，也不会直接形成患者结论。";
}
function useDeepSeekPreset() {
  $("#settings-form").elements.backend.value = "dsh";
  $("#base-url").value = "https://api.deepseek.com/anthropic";
  $("#model-id").value = "deepseek-flash";
  $("#vision-model").value = "deepseek-flash";
  renderModeFields();
  $("#api-key").focus();
}
function renderCapabilityResults(capabilities) {
  const container = $("#model-capability-results");
  if (!capabilities) {
    container.hidden = true;
    container.replaceChildren();
    return;
  }
  const labels = { consultation: "文字问诊", dsh: "DSH", vision: "图片识别" };
  container.replaceChildren(
    ...Object.entries(labels).map(([key, label]) => {
      const item = capabilities[key] || { message: "未检查" };
      const row = el("div", "model-capability-row");
      row.append(el("strong", "", label), el("span", "", item.message));
      return row;
    }),
  );
  container.hidden = false;
}
async function readCredentialStatus() {
  const bridge = window.chengxinDesktop?.modelCredentials;
  if (!bridge) return { available: false, has_saved_config: false };
  try {
    return await bridge.status();
  } catch {
    return { available: false, has_saved_config: false };
  }
}
function renderCredentialOptions() {
  const status = state.modelCredentials;
  $("#remember-model-control").hidden = !status.available;
  $("#remember-model").checked = status.has_saved_config;
  $("#forget-saved-model").hidden = !status.has_saved_config;
  // "unreadable" (saved file present but the OS keyring cannot decrypt
  // it) must not collapse into the generic no-config copy: the app has
  // silently fallen back to demo mode, and only re-saving the key in
  // this dialog restores the saved configuration.
  $("#credential-storage-hint").textContent =
    status.saved_state === "unreadable"
      ? "这台设备上保存的模型配置当前无法读取（系统凭据存储不可用或解密失败），应用可能已回退到离线演示模式。请重新填写并保存密钥。"
      : status.available
        ? status.has_saved_config
          ? "这台设备已有一份由操作系统加密保存的模型配置。"
          : "勾选后由操作系统安全凭据存储加密保存；应用无法从页面读取已保存密钥。"
        : window.chengxinDesktop
          ? "当前系统没有可用的安全凭据存储，密钥只能用于本次运行。"
          : "浏览器模式不会保存密钥；关闭服务后配置失效。";
}
// -- Subagent per-kind rollout panel (settings view) --
// The settings view applies changes instantly (like the appearance
// radios), so each select posts just its own kind through the shared
// settings channel as {subagent_modes: {kind: mode}}; the server
// answers with the full updated settings, which becomes the new truth.
const SUBAGENT_KINDS = [
  ["case_digest", "病例摘要"],
  ["evidence_lookup", "证据检索"],
  ["second_opinion", "第二意见"],
  ["followup_batch", "随访草稿"],
];
const SUBAGENT_MODE_OPTIONS = [
  ["off", "关闭"],
  ["shadow", "影子"],
  ["advisory", "建议"],
];
function subagentKindMode(kind) {
  const subagents = state.settings?.subagents || {};
  const value = subagents.kinds?.[kind] ?? subagents.mode ?? "off";
  return SUBAGENT_MODE_OPTIONS.some(([mode]) => mode === value) ? value : "off";
}
function subagentMetricsText(kind, metrics) {
  if (!metrics) return "暂无运行记录";
  const parts = [
    `已提议 ${metrics.proposed ?? 0}`,
    `完成 ${metrics.completed ?? 0}`,
  ];
  if ((metrics.partial ?? 0) > 0) parts.push(`部分 ${metrics.partial}`);
  if ((metrics.failed ?? 0) > 0) parts.push(`失败 ${metrics.failed}`);
  if ((metrics.dropped ?? 0) > 0) parts.push(`丢弃 ${metrics.dropped}`);
  // Only second_opinion branches can disagree with the parent engine.
  if (kind === "second_opinion")
    parts.push(`分歧 ${metrics.disagreements ?? 0}`);
  return parts.join(" · ");
}
// S4: the subagent panel only renders once the settings "高级" area is
// opened; metrics text is not fetched for a collapsed panel either.
let subagentAdvancedOpen = false;

function renderSubagentPanel() {
  const container = $("#subagent-kind-rows");
  if (!container || !subagentAdvancedOpen) return;
  container.replaceChildren();
  const metricsByKind = state.subagentMetrics?.kinds || {};
  for (const [kind, label] of SUBAGENT_KINDS) {
    const row = el("div", "subagent-kind-row");
    row.dataset.kind = kind;
    const name = el("div", "subagent-kind-name");
    name.append(el("strong", "", label), el("small", "", kind));
    const select = el("select", "subagent-mode-select");
    select.setAttribute("aria-label", `${label}模式`);
    for (const [value, text] of SUBAGENT_MODE_OPTIONS) {
      const option = el("option", "", text);
      option.value = value;
      select.append(option);
    }
    select.value = subagentKindMode(kind);
    select.addEventListener("change", () => {
      saveSubagentModes({ [kind]: select.value }, select);
    });
    row.append(
      name,
      select,
      el(
        "span",
        "subagent-metrics",
        subagentMetricsText(kind, metricsByKind[kind]),
      ),
    );
    container.append(row);
  }
  const note = $("#subagent-global-note");
  if (note) note.hidden = state.settings?.subagents?.mode !== "off";
}
async function saveSubagentModes(modes, control) {
  const feedback = $("#subagent-panel-feedback");
  if (control) control.disabled = true;
  try {
    state.settings = await api("/api/settings", {
      method: "POST",
      body: { subagent_modes: modes },
    });
    if (feedback) {
      feedback.classList.remove("error");
      feedback.textContent = "子智能体设置已保存。";
    }
  } catch (error) {
    // A failed save must never look successful: the panel re-renders
    // from the last known server state (reverting the control) and the
    // failure is surfaced both inline and as a toast.
    if (feedback) {
      feedback.classList.add("error");
      feedback.textContent = `保存失败：${error.message}`;
    }
    toast(`子智能体设置保存失败：${error.message}`);
  } finally {
    if (control) control.disabled = false;
    renderSubagentPanel();
  }
}
async function refreshSubagentPanel() {
  // Both fetches are best-effort: a metrics failure must never block
  // the settings view, and a settings failure keeps the last values.
  try {
    state.settings = await api("/api/settings");
  } catch {
    /* keep the last known settings */
  }
  if (subagentAdvancedOpen) {
    try {
      state.subagentMetrics = await api("/api/subagents/metrics");
    } catch {
      state.subagentMetrics = null;
    }
  } else {
    state.subagentMetrics = null;
  }
  renderSubagentPanel();
}
async function openSettings() {
  try {
    [state.settings, state.modelCredentials] = await Promise.all([
      api("/api/settings"),
      readCredentialStatus(),
    ]);
  } catch (error) {
    toast(error.message);
    return;
  }
  const form = $("#settings-form");
  form.elements.backend.value = state.settings.backend;
  $("#base-url").value = state.settings.base_url || "";
  $("#model-id").value = state.settings.model || "";
  $("#vision-model").value = state.settings.vision_model || "";
  $("#api-key").value = "";
  $("#model-data-consent-input").checked = false;
  $("#dsh-review-enabled").checked =
    state.settings.dsh_background_review_enabled !== false;
  $("#api-key").placeholder = state.settings.has_api_key
    ? "已设置；留空保留现有密钥"
    : "仅在服务器内存保存";
  $("#key-hint").textContent = state.settings.has_api_key
    ? "服务器已持有密钥；浏览器不会读取它。填写新值可替换。"
    : "密钥不会写入浏览器存储或配置文件。";
  $("#settings-feedback").textContent = "";
  renderCapabilityResults();
  renderCredentialOptions();
  renderModeFields();
  $("#settings-dialog").showModal();
}
function settingsPayload() {
  return {
    backend: $("#settings-form").elements.backend.value,
    base_url: $("#base-url").value,
    model: $("#model-id").value,
    api_key: $("#api-key").value,
    vision_model: $("#vision-model").value,
  };
}
async function configure(testOnly) {
  if (state.busy) {
    toast("请等待当前问诊步骤完成后再修改配置。");
    return;
  }
  const feedback = $("#settings-feedback");
  const payload = settingsPayload();
  if (
    !testOnly &&
    payload.backend !== "demo" &&
    !$("#model-data-consent-input").checked
  ) {
    feedback.classList.add("error");
    feedback.textContent = "请先确认已了解数据会发送到所选模型服务。";
    return;
  }
  if (
    !testOnly &&
    payload.backend !== "demo" &&
    $("#remember-model").checked &&
    !payload.api_key.trim() &&
    !state.modelCredentials.has_saved_config
  ) {
    feedback.classList.add("error");
    feedback.textContent = "首次保存到本机时，请填写 API 密钥。";
    return;
  }
  feedback.classList.remove("error");
  feedback.textContent = testOnly
    ? "正在发送不含患者数据的连接测试…"
    : "正在应用配置…";
  $("#test-connection").disabled = true;
  $("#save-settings").disabled = true;
  try {
    const data = await api(testOnly ? "/api/settings/test" : "/api/settings", {
      method: "POST",
      body: payload,
    });
    if (testOnly) {
      feedback.textContent = data.message;
      renderCapabilityResults(data.capabilities);
    } else {
      const credentials = window.chengxinDesktop?.modelCredentials;
      let persistenceError = null;
      if (credentials) {
        try {
          if (payload.backend === "demo" || !$("#remember-model").checked)
            await credentials.clear();
          else await credentials.save(payload);
        } catch (error) {
          persistenceError = error;
        }
        state.modelCredentials = await readCredentialStatus();
      }
      $("#api-key").value = "";
      state.settings = data;
      await executionUI.updateConfiguration();
      updateMode();
      if (persistenceError) {
        renderCredentialOptions();
        feedback.classList.add("error");
        feedback.textContent = `配置已用于本次运行，但未能保存到本机：${persistenceError.message}`;
        return;
      }
      $("#settings-dialog").close();
      toast("配置已应用。开始新的问诊时使用新后端，已有会谈继续保留。");
    }
  } catch (error) {
    feedback.classList.add("error");
    feedback.textContent = error.message;
  } finally {
    $("#test-connection").disabled = false;
    $("#save-settings").disabled = false;
  }
}

async function forgetSavedModel() {
  if (!window.confirm("删除这台设备保存的模型密钥，并将新问诊恢复为离线模式？"))
    return;
  const feedback = $("#settings-feedback");
  feedback.classList.remove("error");
  feedback.textContent = "正在删除本机密钥…";
  try {
    await window.chengxinDesktop.modelCredentials.clear();
    const data = await api("/api/settings", {
      method: "POST",
      body: { backend: "demo" },
    });
    state.settings = data;
    state.modelCredentials = await readCredentialStatus();
    updateMode();
    renderCredentialOptions();
    $("#settings-dialog").close();
    toast("本机模型密钥已删除，新问诊已恢复离线模式。");
  } catch (error) {
    feedback.classList.add("error");
    feedback.textContent = error.message;
  }
}

async function setBackgroundReviewEnabled(enabled) {
  try {
    const result = await api("/api/reviews/enabled", {
      method: "POST",
      body: { enabled },
    });
    state.settings.dsh_background_review_enabled = result.enabled;
    toast(
      result.enabled
        ? "已启用本次服务的后台复核。"
        : "已关闭后台复核并取消未完成任务。",
    );
  } catch (error) {
    $("#dsh-review-enabled").checked =
      state.settings.dsh_background_review_enabled !== false;
    toast(error.message);
  }
}

async function deleteSession(id) {
  if (!id || state.busy) return;
  if (!window.confirm("删除后将清除本次会谈的记录和后台复核数据，且无法恢复。"))
    return;
  setBusy(true);
  try {
    await api(`/api/sessions/${id}`, { method: "DELETE" });
    composerDrafts.delete(id);
    subjectDrafts.delete(id);
    if (id === state.sessionId) {
      state.pendingMessages.clear();
      state.sessionId = null;
      state.case = null;
      state.reply = null;
      state.events = [];
      state.lastCorrection = null;
      state.reportDraft = null;
      state.handoffText = "";
      $("#messages").replaceChildren();
      $("#welcome").hidden = false;
      $("#composer-context").hidden = false;
      $("#open-report-draft").hidden = true;
      $("#open-handoff").hidden = true;
      document.body.classList.remove("has-session");
      renderInspection();
      navigate("consultation");
    }
    await refreshSessions();
    toast("本次会谈记录已删除。");
  } catch (error) {
    toast(`无法删除记录：${error.message}`);
  } finally {
    setBusy(false);
  }
}
function updateMode() {
  $("#backend-pill").replaceChildren(
    document.createTextNode(modeNames[state.settings.backend] || "运行配置"),
    el("span", "", "⌄"),
  );
  $("#backend-pill").title = "新会谈的运行方式";
}
async function exportSession() {
  if (!state.sessionId) return;
  try {
    await loadEvents();
    const blob = new Blob(
      [
        JSON.stringify(
          {
            exported_at: new Date().toISOString(),
            case: state.case,
            events: visiblePayload(state.events),
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const link = el("a");
    link.href = url;
    link.download = `chengxin-session-${state.sessionId.slice(0, 8)}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("已导出本次会谈及运行事件，推理内容已隐藏。");
  } catch (error) {
    toast(error.message);
  }
}

document
  .querySelectorAll("[data-view]")
  .forEach((node) =>
    node.addEventListener("click", () => navigate(node.dataset.view)),
  );
window.addEventListener("hashchange", () => {
  const view = location.hash.slice(1);
  if (view && view !== state.view) navigate(view);
});
// Settings rail scrollspy: highlight the section currently in view.
(() => {
  const links = [...document.querySelectorAll(".settings-rail a")];
  if (!links.length || !("IntersectionObserver" in window)) return;
  const setActive = (id) =>
    links.forEach((link) =>
      link.classList.toggle("active", link.getAttribute("href") === `#${id}`),
    );
  setActive("settings-appearance");
  links.forEach((link) =>
    link.addEventListener("click", () =>
      setActive(link.getAttribute("href").slice(1)),
    ),
  );
  const spy = new IntersectionObserver(
    (entries) =>
      entries.forEach((entry) => {
        if (entry.isIntersecting) setActive(entry.target.id);
      }),
    { root: document.querySelector("main"), rootMargin: "-15% 0px -70% 0px" },
  );
  links.forEach((link) => {
    const section = document.getElementById(link.getAttribute("href").slice(1));
    if (section) spy.observe(section);
  });
})();
$("#new-session").addEventListener("click", startSession);
$("#workspace-mode").addEventListener("change", (event) =>
  setWorkspaceMode(event.target.value),
);
$("#toggle-task-history").addEventListener("click", () => {
  state.taskHistory = !state.taskHistory;
  syncTaskFocus();
});
$("#template-select").addEventListener("change", templateDescription);
$("#template-form").addEventListener("submit", (event) => {
  event.preventDefault();
  changeComposition({ template_id: $("#template-select").value });
});
$("#capability-select").addEventListener("change", renderCapabilityInputs);
$("#capability-test-form").addEventListener("submit", previewCapability);
$("#start-consultation").addEventListener("click", startSession);
document.querySelectorAll("[data-starter]").forEach((node) =>
  node.addEventListener("click", async () => {
    if (!state.sessionId) await startSession();
    if (state.sessionId) {
      $("#message-input").value = node.dataset.starter;
      $("#message-input").dispatchEvent(new Event("input"));
      $("#message-input").focus();
    }
  }),
);
$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage($("#message-input").value);
});
$("#urgent-help").addEventListener("click", () => {
  if (!state.sessionId || state.priorityBusy) return;
  $("#urgent-help-error").hidden = true;
  $("#urgent-help-dialog").showModal();
  $("#urgent-help-input").focus();
});
for (const selector of ["#urgent-help-close", "#urgent-help-back"])
  $(selector).addEventListener("click", () => $("#urgent-help-dialog").close());
$("#urgent-help-form").addEventListener("submit", requestEmergencyHelp);
$("#message-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#message-input").addEventListener("input", () => {
  const input = $("#message-input");
  if (state.sessionId) composerDrafts.set(state.sessionId, input.value);
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 130)}px`;
  $("#input-count").textContent = `${input.value.length} / 2000`;
});
$("#toggle-facts").addEventListener("click", () => {
  $("#facts-panel").classList.toggle("open");
  if (window.innerWidth <= 980 && $("#facts-panel").classList.contains("open"))
    $("#facts-panel").scrollIntoView({ behavior: "smooth" });
});
// The view switcher lives in the settings view (界面与数据 section);
// its markup sits there natively, so no relocation is needed here.
const syncFactsScrim = () => {
  $("#facts-scrim").hidden = !$("#facts-panel").classList.contains("open");
};
new MutationObserver(syncFactsScrim).observe($("#facts-panel"), {
  attributes: true,
  attributeFilter: ["class"],
});
const closeFactsPanel = () => $("#facts-panel").classList.remove("open");
$("#facts-scrim").addEventListener("click", closeFactsPanel);
$("#facts-close").addEventListener("click", closeFactsPanel);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeFactsPanel();
  $("#sidebar").classList.remove("open");
});
$("#view-current-trace").addEventListener("click", () => navigate("inspect"));
$("#refresh-history").addEventListener("click", () =>
  refreshSessions().catch((error) => toast(error.message)),
);
$("#event-filter").addEventListener("change", (event) => {
  state.filter = event.target.value;
  renderInspection();
});
$("#export-session").addEventListener("click", exportSession);
$("#open-settings").addEventListener("click", openSettings);
$("#settings-advanced-toggle").addEventListener("click", () => {
  subagentAdvancedOpen = !subagentAdvancedOpen;
  const toggle = $("#settings-advanced-toggle");
  const section = $("#settings-subagents");
  toggle.setAttribute("aria-expanded", subagentAdvancedOpen ? "true" : "false");
  section.hidden = !subagentAdvancedOpen;
  if (subagentAdvancedOpen) refreshSubagentPanel();
});
$("#subagent-rollback").addEventListener("click", (event) => {
  saveSubagentModes(
    {
      case_digest: "shadow",
      evidence_lookup: "shadow",
      second_opinion: "shadow",
      followup_batch: "shadow",
    },
    event.currentTarget,
  );
});
$("#backend-pill").addEventListener("click", openSettings);
$("#settings-form").addEventListener("change", renderModeFields);
$("#use-deepseek-preset").addEventListener("click", useDeepSeekPreset);
$("#forget-saved-model").addEventListener("click", forgetSavedModel);
$("#dsh-review-enabled").addEventListener("change", (event) =>
  setBackgroundReviewEnabled(event.target.checked),
);
$("#open-report-draft").addEventListener("click", openReportDraft);
$("#open-handoff").addEventListener("click", openHandoff);
$("#report-form").addEventListener("submit", submitReportDraft);
$("#copy-handoff").addEventListener("click", copyHandoff);
$("#print-handoff").addEventListener("click", () => {
  if (!state.handoff) return;
  const win = openHandoffPrintWindow(state.handoff);
  if (win) win.print();
});
$("#export-handoff-md").addEventListener("click", () => {
  if (!state.handoff) return;
  downloadHandoffFile(state.handoff, "md");
});
$("#export-handoff-txt").addEventListener("click", () => {
  if (!state.handoff) return;
  downloadHandoffFile(state.handoff, "txt");
});
$("#settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  configure(false);
});
$("#test-connection").addEventListener("click", () => configure(true));
$("#edit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const text = $("#edit-value").value.trim();
  if (!text) return;
  setBusy(true);
  const editing = state.editing;
  try {
    const data = await api(
      `/api/sessions/${editing.sessionId}/facts/${editing.key}`,
      { method: "PATCH", body: { text, expected_fact_id: editing.factId } },
    );
    appendMessage(`更正${names[editing.key]}：${text}`, "user");
    if (state.case?.active_task) {
      const prefix = `${state.sessionId}:${state.case.active_task}:`;
      for (const key of taskProgress.keys())
        if (key.startsWith(prefix)) taskProgress.delete(key);
    }
    renderCase(data.state);
    state.lastCorrection = {
      actionId: data.reply.action_id,
      message: `已更新“${names[editing.key]}”，请核对受影响的内容。`,
      key: editing.key,
      factId: data.state.facts[editing.key]?.fact_id,
      sessionId: editing.sessionId,
    };
    renderReply(data.reply);
    $("#edit-dialog").close();
    toast(`已更新“${names[editing.key]}”，摘要已重新整理。`);
    await Promise.all([loadEvents(), refreshSessions()]);
  } catch (error) {
    $("#edit-feedback").textContent = error.message;
    $("#edit-feedback").classList.add("error");
    if (error.code === "stale_fact") {
      // Keep the user's draft and its old version. Never silently retry it
      // against a refreshed fact; close and reopen after reviewing the update.
      await resumeSession(editing.sessionId, true).catch(() => {});
    }
  } finally {
    setBusy(false);
  }
});
$("#edit-new-session").addEventListener("click", () => {
  $("#edit-dialog").close();
  startSession();
});
document
  .querySelectorAll(".close-dialog")
  .forEach((node) =>
    node.addEventListener("click", () => node.closest("dialog").close()),
  );
let followUpSubmitting = false;
$("#followup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!followUpTarget || followUpSubmitting) return;
  const outcome = new FormData(event.target).get("outcome");
  const feedback = $("#followup-feedback");
  if (!outcome) {
    feedback.textContent = "请先选择现在的近况。";
    return;
  }
  // A worsened outcome spawns a re-triage session server-side; a
  // double-click must not record the outcome (or spawn the session)
  // twice while the first request is still in flight.
  followUpSubmitting = true;
  const submitButton = event.target.querySelector("[type=submit]");
  if (submitButton) submitButton.disabled = true;
  try {
    const data = await api(
      `/api/sessions/${followUpTarget.session_id}/follow-up/outcome`,
      {
        method: "POST",
        body: { outcome, note: $("#followup-note").value.trim() },
      },
    );
    await refreshSessions();
    if (data.requires_retriage && data.new_session_id) {
      followUpRetriageId = data.new_session_id;
      $("#followup-retriage").hidden = false;
      feedback.textContent = "已记录近况：情况有变，需要重新分诊。";
    } else {
      $("#followup-dialog").close();
      toast("随访近况已记录");
    }
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    followUpSubmitting = false;
    if (submitButton) submitButton.disabled = false;
  }
});
$("#followup-enter-retriage").addEventListener("click", () => {
  const id = followUpRetriageId;
  $("#followup-dialog").close();
  if (id) resumeSession(id).catch((error) => toast(error.message));
});
$("#settings-dialog").addEventListener("close", () => {
  $("#api-key").value = "";
  $("#model-data-consent-input").checked = false;
  renderCapabilityResults();
});
$("#mobile-menu").addEventListener("click", () =>
  $("#sidebar").classList.toggle("open"),
);
window.addEventListener("resize", () => {
  if (!$("#toast").hidden) positionToast();
});
document.addEventListener("click", (event) => {
  if (
    window.innerWidth <= 700 &&
    !event.target.closest("#sidebar") &&
    !event.target.closest("#mobile-menu")
  )
    $("#sidebar").classList.remove("open");
});
$("#connection-retry")?.addEventListener("click", () => init());
async function init() {
  // Static HTML is visible before module hydration; enable only after handlers exist.
  $("#workspace-mode").disabled = false;
  try {
    const [health, settings, tasks, catalog] = await Promise.all([
      api("/api/health"),
      api("/api/settings"),
      api("/api/tasks"),
      api("/api/capabilities"),
    ]);
    state.settings = settings;
    state.sessionPersistence = settings.storage === "session_store";
    applyRetentionCopy();
    renderSubagentPanel();
    executionUI.configure(health.execution_available);
    state.tasks = tasks.tasks;
    state.catalog = catalog;
    updateMode();
    $("#connection-status").replaceChildren(
      el("span", "status-dot"),
      document.createTextNode("本机服务已连接"),
    );
    $("#connection-alert").hidden = true;
    enforceBannerPriority();
    await refreshSessions();
    // Best-effort: a failed count fetch leaves the badge at its
    // placeholder and must not block startup.
    refreshRecordsCount().catch(() => {});
    renderLibrary();
    renderCapabilities();
    navigate(location.hash.slice(1) || "consultation");
  } catch (error) {
    $("#connection-status").textContent = "服务未连接";
    $("#connection-alert").hidden = false;
    enforceBannerPriority();
    toast(`无法连接本机服务：${error.message}`);
  }
}
init();
