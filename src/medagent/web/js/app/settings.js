// app.js — settings section (split for maintainability).

import { $, modeNames, state, executionUI, el, toast, api } from "./context.js";
import { loadEvents, visiblePayload } from "./views.js";

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

export function renderSubagentPanel() {
  const container = $("#subagent-kind-rows");
  if (!container || !state.subagentAdvancedOpen) return;
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
  if (state.subagentAdvancedOpen) {
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
