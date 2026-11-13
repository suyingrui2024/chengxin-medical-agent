// Bind page events once; reconnection hydrates state without rebinding.

import {
  $,
  historyUI,
  reportDraftUI,
  handoffUI,
  taskProgress,
  composerDrafts,
  names,
  state,
  positionToast,
  toast,
  api,
  followUpState,
} from "./context.js";
import {
  setBusy,
  navigate,
  setWorkspaceMode,
  appendMessage,
  renderReply,
  syncTaskFocus,
} from "./render.js";
import {
  renderCase,
  startSession,
  sendMessage,
  requestEmergencyHelp,
  resumeSession,
} from "./flow.js";
import {
  loadEvents,
  templateDescription,
  renderCapabilityInputs,
  changeComposition,
  previewCapability,
  renderInspection,
} from "./views.js";

import { init } from "./bootstrap.js";
import {
  openSettings,
  exportSession,
  renderModeFields,
  useDeepSeekPreset,
  forgetSavedModel,
  setBackgroundReviewEnabled,
  configure,
  renderCapabilityResults,
  refreshSubagentPanel,
  saveSubagentModes,
} from "./settings.js";

export let followUpSubmitting = false;
export const syncFactsScrim = () => {
  $("#facts-scrim").hidden = !$("#facts-panel").classList.contains("open");
};
export const closeFactsPanel = () => $("#facts-panel").classList.remove("open");
let bound = false;

export function bindApplicationEvents() {
  if (bound) return;
  bound = true;
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
      const section = document.getElementById(
        link.getAttribute("href").slice(1),
      );
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
    $(selector).addEventListener("click", () =>
      $("#urgent-help-dialog").close(),
    );
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
    $("#input-count").textContent = `${input.value.length} 字`;
    // 使用体验评测 F2 复议（2026-11-04）：测试阶段不设上限；超过 2000
    // 字时提示仍是引导（拆分发送或报告粘贴），不是闸门。
    $("#composer-length-hint").hidden = input.value.length <= 2000;
  });
  $("#toggle-facts").addEventListener("click", () => {
    $("#facts-panel").classList.toggle("open");
    if (
      window.innerWidth <= 980 &&
      $("#facts-panel").classList.contains("open")
    )
      $("#facts-panel").scrollIntoView({ behavior: "smooth" });
  });
  // The view switcher lives in the settings view (界面与数据 section);
  // its markup sits there natively, so no relocation is needed here.
  new MutationObserver(syncFactsScrim).observe($("#facts-panel"), {
    attributes: true,
    attributeFilter: ["class"],
  });
  $("#facts-scrim").addEventListener("click", closeFactsPanel);
  $("#facts-close").addEventListener("click", closeFactsPanel);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeFactsPanel();
    $("#sidebar").classList.remove("open");
  });
  $("#view-current-trace").addEventListener("click", () => navigate("inspect"));
  $("#refresh-history").addEventListener("click", () =>
    historyUI.refresh().catch((error) => toast(error.message)),
  );
  $("#event-filter").addEventListener("change", (event) => {
    state.filter = event.target.value;
    renderInspection();
  });
  $("#export-session").addEventListener("click", exportSession);
  $("#open-settings").addEventListener("click", openSettings);
  $("#settings-advanced-toggle").addEventListener("click", () => {
    state.subagentAdvancedOpen = !state.subagentAdvancedOpen;
    const toggle = $("#settings-advanced-toggle");
    const section = $("#settings-subagents");
    toggle.setAttribute(
      "aria-expanded",
      state.subagentAdvancedOpen ? "true" : "false",
    );
    section.hidden = !state.subagentAdvancedOpen;
    if (state.subagentAdvancedOpen) refreshSubagentPanel();
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
  $("#open-report-draft").addEventListener("click", reportDraftUI.open);
  $("#open-handoff").addEventListener("click", handoffUI.open);
  $("#report-form").addEventListener("submit", reportDraftUI.submit);
  $("#copy-handoff").addEventListener("click", handoffUI.copy);
  $("#print-handoff").addEventListener("click", handoffUI.print);
  $("#export-handoff-md").addEventListener("click", () => {
    handoffUI.download("md");
  });
  $("#export-handoff-txt").addEventListener("click", () => {
    handoffUI.download("txt");
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
      await Promise.all([loadEvents(), historyUI.refresh()]);
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
  $("#followup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!followUpState.target || followUpSubmitting) return;
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
        `/api/sessions/${followUpState.target.session_id}/follow-up/outcome`,
        {
          method: "POST",
          body: { outcome, note: $("#followup-note").value.trim() },
        },
      );
      await historyUI.refresh();
      if (data.requires_retriage && data.new_session_id) {
        followUpState.retriageId = data.new_session_id;
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
    const id = followUpState.retriageId;
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
}
