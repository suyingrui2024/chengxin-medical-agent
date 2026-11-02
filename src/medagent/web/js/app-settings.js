// app.js — settings section (split for maintainability).

import {
  openHandoffPrintWindow,
  downloadHandoffFile,
} from "./handoff-print.js";
import {
  $,
  taskProgress,
  composerDrafts,
  subjectDrafts,
  names,
  values,
  modeNames,
  state,
  executionUI,
  el,
  positionToast,
  toast,
  api,
  runtime,
} from "./app-context.js";
import {
  setBusy,
  navigate,
  applyRetentionCopy,
  setWorkspaceMode,
  appendMessage,
  renderReply,
  syncTaskFocus,
  enforceBannerPriority,
} from "./app-render.js";
import {
  renderCase,
  openReportDraft,
  submitReportDraft,
  openHandoff,
  copyHandoff,
  startSession,
  sendMessage,
  requestEmergencyHelp,
  resumeSession,
  refreshSessions,
} from "./app-flow.js";
import {
  refreshRecordsCount,
  renderLibrary,
  loadEvents,
  templateDescription,
  renderCapabilities,
  renderCapabilityInputs,
  changeComposition,
  previewCapability,
  visiblePayload,
  renderInspection,
} from "./app-views.js";

export function renderModeFields() {
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
export function useDeepSeekPreset() {
  $("#settings-form").elements.backend.value = "dsh";
  $("#base-url").value = "https://api.deepseek.com/anthropic";
  $("#model-id").value = "deepseek-flash";
  $("#vision-model").value = "deepseek-flash";
  renderModeFields();
  $("#api-key").focus();
}
export function renderCapabilityResults(capabilities) {
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
export async function readCredentialStatus() {
  const bridge = window.chengxinDesktop?.modelCredentials;
  if (!bridge) return { available: false, has_saved_config: false };
  try {
    return await bridge.status();
  } catch {
    return { available: false, has_saved_config: false };
  }
}
export function renderCredentialOptions() {
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
export const SUBAGENT_KINDS = [
  ["case_digest", "病例摘要"],
  ["evidence_lookup", "证据检索"],
  ["second_opinion", "第二意见"],
  ["followup_batch", "随访草稿"],
];
export const SUBAGENT_MODE_OPTIONS = [
  ["off", "关闭"],
  ["shadow", "影子"],
  ["advisory", "建议"],
];
export function subagentKindMode(kind) {
  const subagents = state.settings?.subagents || {};
  const value = subagents.kinds?.[kind] ?? subagents.mode ?? "off";
  return SUBAGENT_MODE_OPTIONS.some(([mode]) => mode === value) ? value : "off";
}
export function subagentMetricsText(kind, metrics) {
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
export let subagentAdvancedOpen = false;

export function renderSubagentPanel() {
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
export async function saveSubagentModes(modes, control) {
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
export async function refreshSubagentPanel() {
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
export async function openSettings() {
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
export function settingsPayload() {
  return {
    backend: $("#settings-form").elements.backend.value,
    base_url: $("#base-url").value,
    model: $("#model-id").value,
    api_key: $("#api-key").value,
    vision_model: $("#vision-model").value,
  };
}
export async function configure(testOnly) {
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

export async function forgetSavedModel() {
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

export async function setBackgroundReviewEnabled(enabled) {
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

export async function deleteSession(id) {
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
export function updateMode() {
  $("#backend-pill").replaceChildren(
    document.createTextNode(modeNames[state.settings.backend] || "运行配置"),
    el("span", "", "⌄"),
  );
  $("#backend-pill").title = "新会谈的运行方式";
}
export async function exportSession() {
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
export const syncFactsScrim = () => {
  $("#facts-scrim").hidden = !$("#facts-panel").classList.contains("open");
};
new MutationObserver(syncFactsScrim).observe($("#facts-panel"), {
  attributes: true,
  attributeFilter: ["class"],
});
export const closeFactsPanel = () => $("#facts-panel").classList.remove("open");
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
export let followUpSubmitting = false;
$("#followup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!runtime.followUpTarget || followUpSubmitting) return;
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
      `/api/sessions/${runtime.followUpTarget.session_id}/follow-up/outcome`,
      {
        method: "POST",
        body: { outcome, note: $("#followup-note").value.trim() },
      },
    );
    await refreshSessions();
    if (data.requires_retriage && data.new_session_id) {
      runtime.followUpRetriageId = data.new_session_id;
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
  const id = runtime.followUpRetriageId;
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
