import {
  createRenderer,
  replyEnvelope,
  calculationEnvelope,
} from "./structured-ui.js";
import { createExecutionUI } from "./execution-ui.js";
import { renderLocationChips } from "./location-answer-ui.js";

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
  tasks: [],
  settings: { backend: "demo" },
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
  shownCalculations: new Map(),
};
let pollTimer = null;
let toastTimer = null;
const executionUI = createExecutionUI(api);

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
function toast(text) {
  clearTimeout(toastTimer);
  $("#toast").textContent = text;
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
  const payload = await response.json();
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
  $("#busy-indicator").hidden = !busy;
  $("#messages").setAttribute("aria-busy", String(busy));
  document
    .querySelectorAll(
      ".quick-replies button,.fact-edit,.recent-item,.history-card,.starter-card",
    )
    .forEach((node) => {
      node.disabled = busy;
    });
  clearInterval(pollTimer);
  pollTimer = null;
  if (busy && state.sessionId) {
    pollTimer = setInterval(() => loadEvents().catch(() => {}), 1100);
    scrollBottom();
  } else {
    clearInterval(pollTimer);
    pollTimer = null;
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
          ? "本次问诊已整理"
          : "核对记录 · 可以更正"
        : "当前这一步";
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
    ![
      "consultation",
      "history",
      "tasks",
      "inspect",
      "capabilities",
      "execution",
    ].includes(view)
  )
    return;
  if (
    state.workspaceMode !== "developer" &&
    ["tasks", "inspect", "capabilities", "execution"].includes(view)
  )
    view = "consultation";
  state.view = view;
  $("#workspace-storage-note").textContent =
    view === "execution" ? "文件与任务记录保存在本机" : "会谈仅保留到服务关闭";
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
    history: "会谈记录",
    tasks: "图示任务库",
    inspect: "运行检查",
    capabilities: "能力与模板",
    execution: "执行工作台",
  }[view];
  $("#sidebar").classList.remove("open");
  if (view === "history")
    refreshSessions().catch((error) => toast(error.message));
  if (view === "tasks") renderLibrary();
  if (view === "execution") executionUI.refresh();
  if (view === "capabilities")
    refreshCapabilities().catch((error) => toast(error.message));
  if (view === "inspect") {
    renderInspection();
    loadEvents().catch((error) => toast(error.message));
  }
  history.replaceState(null, "", `#${view}`);
}

function setWorkspaceMode(mode) {
  state.workspaceMode = mode === "developer" ? "developer" : "patient";
  document.body.dataset.mode = state.workspaceMode;
  $("#workspace-mode").value = state.workspaceMode;
  navigate(state.view);
}

function appendMessage(text, role, kind = "") {
  const row = el("div", `message ${role} ${kind}`);
  const avatar = el("span", "message-avatar");
  if (role === "user") avatar.textContent = "你";
  else {
    const image = el("img");
    image.src = "brand.svg";
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
    onChoose: (label, value) => {
      if (current()) sendMessage(label, value);
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
function summaryContext(readOnly = false, actionId = state.reply?.action_id) {
  const sessionId = state.sessionId;
  return {
    readOnly,
    actionId,
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
  };
}
function syncSummaryEdits() {
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
  for (const block of structuredUI.blocks(replyEnvelope(reply))) {
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
          summaryContext(replay || reply.phase === "closed", reply.action_id),
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
    $("#conversation-status").textContent = "补充待处理";
  }
  const task = state.reply?.task;
  const active = Boolean(
    task &&
    state.case?.active_task === task.id &&
    state.case?.phase !== "closed",
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
          ? "本次问诊已整理"
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
    severity: "也可以描述对吃饭、睡觉和活动的影响",
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
  $("#urgent-help").hidden = data.phase === "closed";
  document
    .querySelector(".conversation-actions")
    .classList.toggle("show-help", data.phase !== "closed");
  document.body.classList.add("has-session");
  $("#welcome").hidden = true;
  const phase = data.phase;
  const risk = data.risk?.level;
  const status =
    risk === "emergency"
      ? "立即求助"
      : risk === "urgent"
        ? "尽快评估"
        : phaseNames[phase] || "进行中";
  $("#phase-label").textContent = status;
  $("#conversation-status").textContent = status;
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
  $("#activity-title").textContent = activity[0];
  $("#activity-reason").textContent = activity[1];
  $("#view-consultation h1").textContent = ["emergency", "urgent"].includes(
    risk,
  )
    ? "优先求助"
    : phase === "observation"
      ? "观察与记录"
      : phase === "summary"
        ? "核对本次记录"
        : phase === "closed"
          ? "本次问诊已整理"
          : "本次问诊";
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
  updateComposer();
  (data.calculations || []).forEach(appendCalculation);
  syncTaskFocus();
  renderInspection();
}
function emptyState(message) {
  const node = el("div", "empty-state compact");
  node.append(icon("cards"), el("p", "", message));
  return node;
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
  $("#conversation-subtitle").textContent = subject
    ? `${subject.label} · ${subject.category === "self" ? "本次问诊" : "家属代问"} · ${mode}`
    : prepared
      ? `${prepared.label} · 原话待发送 · ${mode}`
      : `${mode} · 本次会谈`;
  $("#conversation-subtitle").title = subject
    ? "按用户原话报告的就诊对象，未核验身份。"
    : "";
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
async function refreshSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.sessions;
  renderRecent();
  renderHistory();
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
    $("#history-list").append(card);
  }
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
    image.src = task.asset;
    image.alt = `${task.title}图示预览`;
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

async function loadEvents() {
  const id = state.sessionId;
  if (!id) return;
  const data = await api(`/api/sessions/${id}/events`);
  if (state.sessionId !== id) return;
  if (data.events.length < state.events.length) return;
  state.events = data.events;
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
  "triage.assessed": "重新评估风险",
  "tool.requested": "请求医疗工具",
  "tool.completed": "工具执行完成",
  "model.requested": "提交模型请求",
  "model.responded": "收到模型响应",
  "model.failed": "模型调用失败",
  "agent.backend_failed": "决策服务失败",
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
}
async function openSettings() {
  try {
    state.settings = await api("/api/settings");
  } catch (error) {
    toast(error.message);
    return;
  }
  const form = $("#settings-form");
  form.elements.backend.value = state.settings.backend;
  $("#base-url").value = state.settings.base_url || "";
  $("#model-id").value = state.settings.model || "";
  $("#api-key").value = "";
  $("#api-key").placeholder = state.settings.has_api_key
    ? "已设置；留空保留现有密钥"
    : "仅在服务器内存保存";
  $("#key-hint").textContent = state.settings.has_api_key
    ? "服务器已持有密钥；浏览器不会读取它。填写新值可替换。"
    : "密钥不会写入浏览器存储或配置文件。";
  $("#settings-feedback").textContent = "";
  renderModeFields();
  $("#settings-dialog").showModal();
}
function settingsPayload() {
  return {
    backend: $("#settings-form").elements.backend.value,
    base_url: $("#base-url").value,
    model: $("#model-id").value,
    api_key: $("#api-key").value,
  };
}
async function configure(testOnly) {
  if (state.busy) {
    toast("请等待当前问诊步骤完成后再修改配置。");
    return;
  }
  const feedback = $("#settings-feedback");
  feedback.classList.remove("error");
  feedback.textContent = testOnly
    ? "正在发送不含患者数据的连接测试…"
    : "正在应用配置…";
  $("#test-connection").disabled = true;
  $("#save-settings").disabled = true;
  try {
    const data = await api(testOnly ? "/api/settings/test" : "/api/settings", {
      method: "POST",
      body: settingsPayload(),
    });
    if (testOnly) {
      feedback.textContent = data.message;
    } else {
      $("#api-key").value = "";
      state.settings = data;
      await executionUI.updateConfiguration();
      updateMode();
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
function updateMode() {
  $("#patient-mode-label").textContent =
    modeNames[state.settings.backend] || "模型辅助";
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
$("#backend-pill").addEventListener("click", openSettings);
$("#settings-form").addEventListener("change", renderModeFields);
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
    renderReply(data.reply);
    $("#edit-dialog").close();
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
$("#settings-dialog").addEventListener("close", () => {
  $("#api-key").value = "";
});
$("#mobile-menu").addEventListener("click", () =>
  $("#sidebar").classList.toggle("open"),
);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("#sidebar").classList.remove("open");
});
document.addEventListener("click", (event) => {
  if (
    window.innerWidth <= 700 &&
    !event.target.closest("#sidebar") &&
    !event.target.closest("#mobile-menu")
  )
    $("#sidebar").classList.remove("open");
});
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
    executionUI.configure(health.execution_available);
    state.tasks = tasks.tasks;
    state.catalog = catalog;
    updateMode();
    $("#connection-status").replaceChildren(
      el("span", "status-dot"),
      document.createTextNode("本机服务已连接"),
    );
    await refreshSessions();
    renderLibrary();
    renderCapabilities();
    navigate(location.hash.slice(1) || "consultation");
  } catch (error) {
    $("#connection-status").textContent = "服务未连接";
    toast(`无法连接本机服务：${error.message}`);
  }
}
init();
