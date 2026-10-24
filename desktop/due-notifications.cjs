// Due-center notification logic (design doc §6.2, decision D4).
//
// Pure module: no Electron require, so node --test can load it directly.
// The main process (main.cjs) owns polling + Electron Notification; this
// module owns the one rule everything else must agree on — what counts as
// due — and the de-duplication state.
//
// Due rule (shared with the web badge in app.js and workspace.follow_ups):
// a follow-up plan is due when plan.status === "scheduled" and
// plan.due_at parses to a time <= now. A medication reminder is due when
// it is not completed and its due time <= now. Nothing here judges
// severity, dosage, or interaction: reminder bodies name the medication
// and say it is time, nothing more.
//
// De-duplication: notified state holds only the last notified *group key*
// per channel (the sorted due keys joined). The same due set therefore
// notifies exactly once, no matter how often the checker runs; the set
// changing (a new item becomes due, or a due item is completed) produces
// a new key and one fresh notification. State is bounded (two strings),
// never a growing log, and a corrupt state file fails open to "nothing
// notified yet" rather than blocking notifications.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const STATE_FILENAME = "due-notifications.json";

function defaultState() {
  return { version: 1, followups: null, medications: null };
}

function loadNotifiedState(userDataDir) {
  try {
    const raw = fs.readFileSync(path.join(userDataDir, STATE_FILENAME), "utf8");
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return defaultState();
    return {
      version: 1,
      followups: typeof parsed.followups === "string" ? parsed.followups : null,
      medications:
        typeof parsed.medications === "string" ? parsed.medications : null,
    };
  } catch {
    return defaultState();
  }
}

function saveNotifiedState(userDataDir, state) {
  const file = path.join(userDataDir, STATE_FILENAME);
  const tmp = `${file}.tmp`;
  fs.writeFileSync(
    tmp,
    JSON.stringify({
      ...state,
      version: 1,
      updated_at: new Date().toISOString(),
    }),
    "utf8",
  );
  fs.renameSync(tmp, file);
}

function isDuePlan(plan, nowMs) {
  if (!plan || plan.status !== "scheduled") return false;
  const dueAt = Date.parse(plan.due_at);
  return Number.isFinite(dueAt) && dueAt <= nowMs;
}

function dueFollowUps(items, nowMs) {
  return (items || []).filter((item) => isDuePlan(item && item.plan, nowMs));
}

function followUpKey(item) {
  return `${item.session_id || "unknown"}:${(item.plan || {}).due_at || ""}`;
}

// Medication-reminder entries (produced by P4-B's medication_reminders.py
// and served to the desktop/web clients). Two shapes are accepted:
//   (a) timestamped entries {medication_name, due_at, status} — the
//       generic serving shape;
//   (b) P4-B's plan entries {medication_name, scheduled_times: ["08:00"],
//       status: "scheduled"|"pending_setup"} — a plan time is "due" once
//       today's local wall-clock occurrence of that time has passed.
//       There is no taken/completed tracking in the plan, so a due
//       occurrence stays due until midnight; the group key below
//       (name + calendar date + clock time) still guarantees exactly one
//       notification per occurrence. The adapter is tolerant of field
//       spellings but strict on the two things that matter: a parseable
//       due time, and never reading any dose field.
function normalizeReminder(entry) {
  if (!entry || typeof entry !== "object") return null;
  const id = entry.reminder_id || entry.id || null;
  const name = entry.medication_name || entry.name || null;
  const dueAt = entry.due_at || entry.remind_at || entry.scheduled_at || null;
  const completed =
    entry.status === "completed" ||
    entry.status === "done" ||
    entry.done === true ||
    entry.outcome != null;
  if (!name || !dueAt || !Number.isFinite(Date.parse(dueAt))) return null;
  return { id, name: String(name), due_at: String(dueAt), completed };
}

function planOccurrences(entry, nowMs) {
  // Expand one P4-B plan entry into today's occurrences. Returns [] for
  // pending_setup entries and for entries without valid clock times.
  if (!entry || typeof entry !== "object") return [];
  if (entry.status !== "scheduled") return [];
  const name = entry.medication_name || entry.name;
  if (!name || !Array.isArray(entry.scheduled_times)) return [];
  const now = new Date(nowMs);
  const out = [];
  for (const clock of entry.scheduled_times) {
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
      0,
      0,
    );
    if (occurrence.getTime() <= nowMs) {
      const date = `${occurrence.getFullYear()}-${String(occurrence.getMonth() + 1).padStart(2, "0")}-${String(occurrence.getDate()).padStart(2, "0")}`;
      out.push({
        id: entry.reminder_id || entry.id || null,
        name: String(name),
        due_at: occurrence.toISOString(),
        completed: false,
        occurrenceKey: `${String(name)}:${date}T${clock.trim()}`,
      });
    }
  }
  return out;
}

function dueReminders(entries, nowMs) {
  const out = [];
  for (const entry of entries || []) {
    const occurrences = planOccurrences(entry, nowMs);
    if (occurrences.length > 0) {
      out.push(...occurrences);
      continue;
    }
    const reminder = normalizeReminder(entry);
    if (reminder && !reminder.completed && Date.parse(reminder.due_at) <= nowMs)
      out.push(reminder);
  }
  return out;
}

function reminderKey(reminder) {
  if (reminder.occurrenceKey) return `medication:${reminder.occurrenceKey}`;
  return `${reminder.id || reminder.name}:${reminder.due_at}`;
}

function formatFollowUpBody(count) {
  return `${count} 个随访计划已到回访时间，打开「会谈记录」记录近况。`;
}

function formatMedicationBody(names) {
  const shown = names.slice(0, 3).join("、");
  const rest = names.length > 3 ? `等 ${names.length} 种` : "";
  return `该吃${shown}${rest}了。`;
}

function selectDueNotifications({ followUps, reminders, notified, nowMs }) {
  const dueFollow = dueFollowUps(followUps, nowMs);
  const dueRemind = dueReminders(reminders, nowMs);
  const state = notified || defaultState();
  const toShow = [];
  const next = { ...state };

  if (dueFollow.length > 0) {
    const key = dueFollow.map(followUpKey).sort().join(",");
    if (state.followups !== key) {
      toShow.push({
        channel: "followup",
        key,
        title: "有随访到期",
        body: formatFollowUpBody(dueFollow.length),
        target: "followups",
      });
      next.followups = key;
    }
  } else {
    next.followups = null;
  }

  if (dueRemind.length > 0) {
    const key = dueRemind.map(reminderKey).sort().join(",");
    if (state.medications !== key) {
      toShow.push({
        channel: "medication",
        key,
        title: "该吃药了",
        body: formatMedicationBody(dueRemind.map((item) => item.name)),
        target: "medications",
      });
      next.medications = key;
    }
  } else {
    next.medications = null;
  }

  return { toShow, nextNotified: next };
}

// Checker orchestration for main.cjs: polling, notification display and
// click-through are injected so tests can run this without Electron.
// fetchJson(url) resolves to parsed JSON (or throws); notify(payload,
// onClick) shows one system notification; openTarget(target) focuses the
// window and routes the renderer to the due list.
function createDueChecker({
  fetchJson,
  notify,
  openTarget,
  loadState,
  saveState,
  followUpsUrl,
  remindersUrl,
  intervalMs = 5 * 60 * 1000,
}) {
  let timer = null;
  let lastRun = 0;
  async function check(nowMs = Date.now()) {
    let followUps = [];
    let reminders = [];
    try {
      const data = await fetchJson(followUpsUrl);
      followUps = (data && data.follow_ups) || [];
    } catch {
      followUps = [];
    }
    try {
      const data = await fetchJson(remindersUrl);
      reminders = Array.isArray(data && data.reminders)
        ? data.reminders
        : Array.isArray(data)
          ? data
          : [];
    } catch {
      reminders = [];
    }
    const notified = loadState();
    const { toShow, nextNotified } = selectDueNotifications({
      followUps,
      reminders,
      notified,
      nowMs,
    });
    const shownItems = [];
    for (const item of toShow) {
      try {
        notify(item, () => openTarget(item.target));
        shownItems.push(item);
      } catch {
        // Notifications are best-effort; a display failure must not
        // mark the item as notified — the next check retries it.
      }
    }
    // Persist the post-check state: keys of notifications actually shown,
    // cleared channels for nothing-due channels, and untouched keys for
    // channels whose display failed. Saving even when nothing was shown
    // keeps a completed plan from re-matching a stale key later.
    const shown = { ...notified };
    for (const item of shownItems) {
      if (item.channel === "followup") shown.followups = item.key;
      if (item.channel === "medication") shown.medications = item.key;
    }
    if (nextNotified.followups === null) shown.followups = null;
    if (nextNotified.medications === null) shown.medications = null;
    if (JSON.stringify(shown) !== JSON.stringify(notified)) {
      try {
        saveState(shown);
      } catch {
        // State persistence is best-effort; worst case is one repeat.
      }
    }
    lastRun = nowMs;
    return toShow;
  }
  function start() {
    if (timer) return;
    void check();
    timer = setInterval(() => void check(), intervalMs);
  }
  function checkOnFocus(nowMs = Date.now()) {
    // Focus fires often; re-check at most once a minute from this path.
    // The interval above remains the primary cadence.
    if (nowMs - lastRun >= 60 * 1000) void check(nowMs);
  }
  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
  }
  return { check, start, stop, checkOnFocus };
}

module.exports = {
  STATE_FILENAME,
  defaultState,
  loadNotifiedState,
  saveNotifiedState,
  isDuePlan,
  dueFollowUps,
  normalizeReminder,
  planOccurrences,
  dueReminders,
  selectDueNotifications,
  formatFollowUpBody,
  formatMedicationBody,
  createDueChecker,
};
