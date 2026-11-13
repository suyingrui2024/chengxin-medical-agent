// DOM/API services and explicit feature construction for the application.

import { $ } from "../dom.js";
export { $ };
export {
  textElement as el,
  action as button,
  icon,
  emptyState,
} from "../dom.js";
import { createRenderer } from "../structured/structured-ui.js";
import { createExecutionUI } from "../execution/execution-ui.js";
import { createPatientRecordsUI } from "../records/records-ui.js";
import { createReportDraftUI } from "./report-draft.js";
import { createHandoffUI } from "./handoff.js";
import { createHistoryUI } from "./history.js";

import { taskProgress, state } from "./state.js";
import { names, values } from "./labels.js";
export {
  taskProgress,
  calculationDrafts,
  composerDrafts,
  interactionDrafts,
  subjectDrafts,
  state,
  followUpState,
} from "./state.js";
export {
  names,
  values,
  sourceNames,
  qualityNames,
  modeNames,
  phaseNames,
} from "./labels.js";

export let toastTimer = null;
export let structuredUI;
export let executionUI;
export let patientRecordsUI;
export let reportDraftUI;
export let handoffUI;
export let historyUI;
let initialized = false;

export function initializeContext({
  navigate,
  renderCase,
  setBusy,
  resumeSession,
  refreshFollowUps,
  onSessionDeleted,
}) {
  if (initialized) return;
  structuredUI = createRenderer({ taskProgress });
  executionUI = createExecutionUI(api);
  reportDraftUI = createReportDraftUI({
    api,
    toast,
    setBusy,
    renderCase,
    formatTime,
  });
  handoffUI = createHandoffUI({
    api,
    toast,
    reportItemText: reportDraftUI.itemText,
  });
  historyUI = createHistoryUI({
    api,
    toast,
    setBusy,
    formatTime,
    resumeSession,
    refreshFollowUps,
    onSessionDeleted,
  });
  patientRecordsUI = createPatientRecordsUI(api, toast, {
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

  initialized = true;
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
