// Session history owns list refresh ordering and deletion requests.
import {
  $,
  textElement as el,
  action as button,
  icon,
  emptyState,
} from "../dom.js";
import { state, subjectDrafts } from "./state.js";
import { modeNames, phaseNames } from "./labels.js";

export function createHistoryUI({
  api,
  toast,
  setBusy,
  formatTime,
  resumeSession,
  refreshFollowUps,
  onSessionDeleted,
}) {
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
      // 同主诉的几次问诊否则长得一模一样：每条都带上开始时间（R3-10）。
      const shown = `${title} · ${formatTime(item.created_at)}`;
      const node = button(
        shown,
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
  async function deleteSession(id) {
    if (!id || state.busy) return;
    if (
      !window.confirm("删除后将清除本次会谈的记录和后台复核数据，且无法恢复。")
    )
      return;
    setBusy(true);
    try {
      await api(`/api/sessions/${id}`, { method: "DELETE" });
      onSessionDeleted(id);
      await refreshSessions();
      toast("本次会谈记录已删除。");
    } catch (error) {
      toast(`无法删除记录：${error.message}`);
    } finally {
      setBusy(false);
    }
  }

  return { refresh: refreshSessions, renderRecent, renderHistory };
}
