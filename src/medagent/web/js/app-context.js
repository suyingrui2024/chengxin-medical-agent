// app.js — context section (split for maintainability).

import { createRenderer } from "./structured-ui.js";
import { createExecutionUI } from "./execution-ui.js";
import { createPatientRecordsUI } from "./patient-records-ui.js";

export const $ = (selector) => document.querySelector(selector);
export const taskProgress = new Map();
export const calculationDrafts = new Map();
export const composerDrafts = new Map();
export const interactionDrafts = new Map();
export const subjectDrafts = new Map();
export const structuredUI = createRenderer({ taskProgress });
export const names = {
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
export const values = {
  self: "本人",
  child: "孩子",
  other_adult: "其他家人",
  worsening: "加重",
  improving: "减轻",
  stable: "变化不大",
  intermittent: "阵发性",
};
export const sourceNames = {
  patient: "患者自述",
  caregiver: "照护者自述",
  device: "设备直连",
  camera: "相机观察",
  system_inference: "系统推断",
};
export const qualityNames = {
  not_assessed: "质量未评价",
  adequate: "质量可用",
  inadequate: "质量不足",
  not_done: "未执行",
};
export const modeNames = {
  demo: "离线演示",
  compatible: "直接模型",
  dsh: "DSH 问诊",
};
export const phaseNames = {
  opening: "明确问题",
  emergency_screen: "紧急信号",
  history: "了解经过",
  background: "补充背景",
  observation: "定向观察",
  summary: "待核对",
  closed: "已结束",
};
export const phaseOrder = [
  "opening",
  "emergency_screen",
  "history",
  "background",
  "observation",
  "closed",
];
export const state = {
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
export let toastTimer = null;
export const executionUI = createExecutionUI(api);
export const patientRecordsUI = createPatientRecordsUI(api, toast, {
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
  openConsultation: () => runtime.navigateImpl("consultation"),
  onAttached: (data) => {
    runtime.renderCaseImpl(data.state);
    runtime.navigateImpl("consultation");
    toast(`已将 ${data.items.length} 项确认数据用于本次问诊。`);
  },
  onBound: (data) => {
    if (data?.state) runtime.renderCaseImpl(data.state);
  },
});

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
export function icon(name) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("class", "icon");
  node.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  node.append(use);
  return node;
}
export function button(label, className, handler) {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
export function positionToast() {
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
export function toast(text) {
  clearTimeout(toastTimer);
  $("#toast").textContent = text;
  positionToast();
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 4500);
}
export async function api(path, { method = "GET", body, signal } = {}) {
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
export function factLabel(key) {
  const task = state.case?.runtime_snapshot?.composition?.capabilities.find(
    (item) => item.task?.result_key === key,
  )?.task;
  return names[key] || task?.result_label || task?.title || key;
}
export function displayFact(key, fact) {
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
export function formatTime(raw, date = false) {
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

// Mutable state shared across feature modules.
export const runtime = {
  followUpTarget: null,
  followUpRetriageId: null,
  navigateImpl: null,
  renderCaseImpl: null,
};
