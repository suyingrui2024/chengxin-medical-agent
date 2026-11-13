// Explicit feature composition and service-state hydration.

import {
  $,
  historyUI,
  state,
  executionUI,
  el,
  toast,
  api,
  initializeContext,
} from "./context.js";
import {
  navigate,
  applyRetentionCopy,
  enforceBannerPriority,
  setBusy,
} from "./render.js";
import {
  renderCase,
  resumeSession,
  lastSessionId,
  onSessionDeleted,
} from "./flow.js";
import {
  refreshRecordsCount,
  renderLibrary,
  renderCapabilities,
  refreshFollowUps,
} from "./views.js";

import { bindApplicationEvents } from "./events.js";
import { updateMode, renderSubagentPanel } from "./settings.js";

export function boot() {
  initializeContext({
    navigate,
    renderCase,
    setBusy,
    resumeSession,
    refreshFollowUps,
    onSessionDeleted,
  });
  bindApplicationEvents();
  return init();
}

// 刚结束的问诊，重开应用还留在原处；超过这个窗口就从首页开始。
// 患者关掉应用半小时后回来，多半是想再看一遍结论；隔了一天再来，
// 那是新的一次就医，回到首页更顺（使用体验评测 R3-09，2026-11-05）。
const REOPEN_CLOSED_WITHIN_MS = 24 * 60 * 60 * 1000;

function worthReopening(item, now = Date.now()) {
  // 进行中的问诊不限窗口——它本来就还没结束。
  if (item.phase !== "closed") return true;
  const stamp = Date.parse(item.updated_at ?? "");
  if (Number.isNaN(stamp)) return false;
  return now - stamp <= REOPEN_CLOSED_WITHIN_MS;
}

export async function init() {
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
    await historyUI.refresh();
    // 使用体验评测 F3（2026-11-04）/ R3-09（2026-11-05）：刷新或重开应用
    // 曾落在开始页，患者得自己去侧栏找回会话。恢复范围只限**本浏览器**
    // 记住的那一次——进行中不限，已结束的限一天之内；服务器上其他会话
    // （别的标签页、自动化残留）不参与。
    const stored = lastSessionId();
    const active = stored
      ? state.sessions.find(
          (item) => item.session_id === stored && worthReopening(item),
        )
      : undefined;
    if (active && !state.sessionId) {
      await resumeSession(active.session_id, true).catch(() => {});
    }
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
    // 使用体验评测 R3-13：fetch 的网络错误是英文（Failed to fetch），
    // 而且只说了"连不上"。换成患者能懂的说明 + 下一步。
    const reason = /Failed to fetch|NetworkError|Load failed/i.test(
      error.message ?? "",
    )
      ? "本机服务没有响应"
      : error.message || "未知错误";
    toast(
      `无法连接本机服务：${reason}。请确认桌面应用仍在运行（或服务已在 8765 端口启动），然后点「重新连接」。`,
    );
  }
}
