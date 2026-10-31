// app.js — views section (split for maintainability).

import { replyEnvelope, calculationEnvelope } from "./structured-ui.js";
import {
  $,
  structuredUI,
  names,
  values,
  sourceNames,
  qualityNames,
  phaseNames,
  state,
  el,
  button,
  toast,
  api,
  factLabel,
  displayFact,
  formatTime,
  runtime,
} from "./app-context.js";
import {
  setBusy,
  navigate,
  appendMessage,
  renderTask,
  summaryContext,
  renderReply,
  syncTaskFocus,
} from "./app-render.js";
import {
  renderCase,
  emptyState,
  resumeSession,
  refreshSessions,
  FOLLOW_UP_KIND_LABELS,
} from "./app-flow.js";
import { configure } from "./app-settings.js";

export async function refreshFollowUps() {
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
export function dueReminderNames() {
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
export function dueReminderCount() {
  return dueReminderNames().length;
}
export async function refreshRecordsCount() {
  // The badge in the nav is static 0 in the HTML and the records module
  // only corrects it when the records view is opened; pull just the
  // timeline up front so the first screen shows the real count.
  const data = await api("/api/patient/timeline");
  $("#records-count").textContent = String((data.entries || []).length);
}
export function followUpDueCount() {
  const now = Date.now();
  return (state.followUps || []).filter((item) => {
    if (!item.plan || item.plan.status !== "scheduled") return false;
    const dueAt = Date.parse(item.plan.due_at);
    return Number.isFinite(dueAt) && dueAt <= now;
  }).length;
}
export function renderFollowUpBadge() {
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
// v1.3 §13.5(a): focus the existing follow-up card. The close flow
// already scheduled the plan (consultation._schedule_follow_up) and
// rendered its card into the transcript, so this button never creates
// state — it scrolls to the last transcript follow-up card and gives
// it keyboard focus. When the transcript holds no such card (e.g. a
// reopened view), the fallback is the history follow-up section,
// which aggregates the same scheduled plan — still a real action,
// never a no-op.
export function focusFollowUpCard() {
  const cards = document.querySelectorAll("#messages .followup-card");
  const target = cards[cards.length - 1];
  if (target) {
    target.scrollIntoView({ block: "start" });
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
    return true;
  }
  navigate("history");
  document
    .getElementById("followup-section")
    ?.scrollIntoView({ block: "start" });
  return false;
}
export function renderFollowUps() {
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
export function openFollowUpDialog(item) {
  runtime.followUpTarget = item;
  runtime.followUpRetriageId = null;
  $("#followup-context").textContent =
    `${item.title || "上次问诊"} · ${FOLLOW_UP_KIND_LABELS[item.plan.kind] || "随访"}（约定 ${formatTime(item.plan.due_at, true)}）`;
  $("#followup-form").reset();
  $("#followup-feedback").textContent = "";
  $("#followup-retriage").hidden = true;
  $("#followup-dialog").showModal();
}
export function renderLibrary() {
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

export function refreshConclusionCard(conclusion) {
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

export async function loadEvents() {
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

export function renderCalculation(result) {
  const node = el("div", "calculation-result");
  node.append(structuredUI.renderEnvelope(calculationEnvelope(result)));
  return node;
}
export function appendCalculation(result) {
  const key = result.execution_id || JSON.stringify(result.input);
  const signature = JSON.stringify(calculationEnvelope(result));
  const previous = state.shownCalculations.get(key);
  if (previous?.signature === signature && previous.node.isConnected) return;
  const node = renderCalculation(result);
  if (previous?.node.isConnected) previous.node.replaceWith(node);
  else $("#messages").append(node);
  state.shownCalculations.set(key, { signature, node });
}
export async function refreshCapabilities() {
  const [catalog, tasks] = await Promise.all([
    api("/api/capabilities"),
    api("/api/tasks"),
  ]);
  state.catalog = catalog;
  state.tasks = tasks.tasks;
  renderCapabilities();
  renderLibrary();
}
export function templateDescription() {
  const template = state.catalog.templates.find(
    (item) => item.id === $("#template-select").value,
  );
  $("#template-description").textContent = template?.description || "";
}
export function renderCapabilities() {
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
export function renderCapabilityInputs() {
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
export async function changeComposition(change) {
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
export function setCapabilityBusy(busy) {
  state.capabilityBusy = busy;
  $("#view-capabilities")
    .querySelectorAll("button,input,select")
    .forEach((node) => {
      node.disabled = busy;
    });
  if (!busy)
    $("#run-capability-test").disabled = !$("#capability-select").value;
}
export async function previewCapability(event) {
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
export const eventLabels = {
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
export function eventTitle(event) {
  const p = event.payload;
  if (event.event_type === "fact.recorded")
    return `记录 · ${names[p.key] || p.key}`;
  if (event.event_type === "tool.requested") return `调用 · ${p.tool}`;
  if (event.event_type === "tool.completed")
    return p.result?.ok === false ? `拒绝 · ${p.tool}` : `完成 · ${p.tool}`;
  return eventLabels[event.event_type] || event.event_type;
}
export function visiblePayload(value) {
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
export function renderInspection() {
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
export function openEdit(key, fact) {
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
export async function restoreCorrection(correction) {
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
