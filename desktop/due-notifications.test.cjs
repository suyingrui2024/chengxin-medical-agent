const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  defaultState,
  loadNotifiedState,
  saveNotifiedState,
  isDuePlan,
  dueFollowUps,
  normalizeReminder,
  planOccurrences,
  dueReminders,
  selectDueNotifications,
  formatMedicationBody,
  createDueChecker,
} = require("./due-notifications.cjs");

const NOW = Date.parse("2026-10-24T12:00:00Z");
const PAST = "2026-10-24T10:00:00Z";
const FUTURE = "2026-10-24T14:00:00Z";

function plan(status, due_at) {
  return { status, due_at };
}

test("due rule matches workspace/app.js: scheduled and due_at <= now", () => {
  assert.equal(isDuePlan(plan("scheduled", PAST), NOW), true);
  assert.equal(isDuePlan(plan("scheduled", FUTURE), NOW), false);
  assert.equal(isDuePlan(plan("completed", PAST), NOW), false);
  assert.equal(isDuePlan(plan("scheduled", "not-a-date"), NOW), false);
  assert.equal(isDuePlan(null, NOW), false);
  assert.equal(
    dueFollowUps(
      [
        { session_id: "a", plan: plan("scheduled", PAST) },
        { session_id: "b", plan: plan("scheduled", FUTURE) },
        { session_id: "c", plan: plan("completed", PAST) },
      ],
      NOW,
    ).length,
    1,
  );
});

test("same due set notifies exactly once; a changed set notifies again", () => {
  const followUps = [{ session_id: "a", plan: plan("scheduled", PAST) }];
  const first = selectDueNotifications({
    followUps,
    reminders: [],
    notified: defaultState(),
    nowMs: NOW,
  });
  assert.equal(first.toShow.length, 1);
  assert.equal(first.toShow[0].target, "followups");
  const second = selectDueNotifications({
    followUps,
    reminders: [],
    notified: first.nextNotified,
    nowMs: NOW,
  });
  assert.equal(second.toShow.length, 0);
  // A second plan becomes due: the group key changes, one fresh notice.
  const third = selectDueNotifications({
    followUps: [
      ...followUps,
      { session_id: "b", plan: plan("scheduled", PAST) },
    ],
    reminders: [],
    notified: first.nextNotified,
    nowMs: NOW,
  });
  assert.equal(third.toShow.length, 1);
  assert.match(third.toShow[0].body, /2 个随访计划/);
  // Everything completed: state clears, so a later identical set is new.
  const cleared = selectDueNotifications({
    followUps: [],
    reminders: [],
    notified: first.nextNotified,
    nowMs: NOW,
  });
  assert.equal(cleared.nextNotified.followups, null);
});

test("medication reminders share the channel: name only, never a dose", () => {
  const reminders = [
    { reminder_id: "r1", medication_name: "降压药", due_at: PAST },
    // A dose field, if a producer ever adds one, must never be read.
    {
      reminder_id: "r2",
      medication_name: "维生素",
      due_at: PAST,
      dose: "10mg",
    },
    { reminder_id: "r3", medication_name: "钙片", due_at: FUTURE },
    {
      reminder_id: "r4",
      medication_name: "鱼油",
      due_at: PAST,
      status: "completed",
    },
  ];
  const due = dueReminders(reminders, NOW);
  assert.deepEqual(
    due.map((item) => item.name),
    ["降压药", "维生素"],
  );
  const body = formatMedicationBody(due.map((item) => item.name));
  assert.equal(body, "该吃降压药、维生素了。");
  assert.doesNotMatch(body, /10mg/);
  assert.equal(normalizeReminder({ name: "钙片" }), null);
  assert.equal(
    normalizeReminder({ medication_name: "钙片", due_at: "bad" }),
    null,
  );
  const selected = selectDueNotifications({
    followUps: [],
    reminders,
    notified: defaultState(),
    nowMs: NOW,
  });
  assert.equal(selected.toShow.length, 1);
  assert.equal(selected.toShow[0].channel, "medication");
  assert.equal(selected.toShow[0].target, "medications");
});

test("P4-B plan entries: today's past clock times are due, future are not", () => {
  // Build clock times relative to NOW in the runtime's local timezone,
  // so the test holds regardless of the machine's zone.
  const now = new Date(NOW);
  const fmt = (date) =>
    `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  const past = fmt(new Date(NOW - 60 * 60 * 1000));
  const future = fmt(new Date(NOW + 60 * 60 * 1000));
  const plan = {
    medication_name: "降压药",
    scheduled_times: [past, future],
    status: "scheduled",
  };
  const occurrences = planOccurrences(plan, NOW);
  assert.equal(occurrences.length, 1);
  assert.equal(occurrences[0].name, "降压药");
  assert.equal(
    dueReminders(
      [
        plan,
        {
          medication_name: "维生素",
          scheduled_times: [past],
          status: "pending_setup",
        },
        { medication_name: "钙片", scheduled_times: [], status: "scheduled" },
      ],
      NOW,
    ).length,
    1,
  );
  // One notification per occurrence: same plan, same day, second check silent.
  const selected = selectDueNotifications({
    followUps: [],
    reminders: [plan],
    notified: defaultState(),
    nowMs: NOW,
  });
  assert.equal(selected.toShow.length, 1);
  assert.equal(selected.toShow[0].channel, "medication");
  const again = selectDueNotifications({
    followUps: [],
    reminders: [plan],
    notified: selected.nextNotified,
    nowMs: NOW,
  });
  assert.equal(again.toShow.length, 0);
});

test("corrupt notified state fails open; round-trip persists", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "due-notifications-"));
  assert.deepEqual(loadNotifiedState(dir), defaultState());
  fs.writeFileSync(path.join(dir, "due-notifications.json"), "{broken");
  assert.deepEqual(loadNotifiedState(dir), defaultState());
  saveNotifiedState(dir, { followups: "a:2026", medications: null });
  assert.equal(loadNotifiedState(dir).followups, "a:2026");
  fs.rmSync(dir, { recursive: true, force: true });
});

test("checker: failed fetch is empty, failed display is retried, click routes", async () => {
  const shown = [];
  const opened = [];
  let state = defaultState();
  const checker = createDueChecker({
    fetchJson: async (url) => {
      if (url.endsWith("/follow-ups"))
        return {
          follow_ups: [{ session_id: "a", plan: plan("scheduled", PAST) }],
        };
      throw new Error("404");
    },
    notify: (item, onClick) => {
      shown.push(item);
      onClick();
    },
    openTarget: (target) => opened.push(target),
    loadState: () => state,
    saveState: (next) => {
      state = next;
    },
    followUpsUrl: "http://127.0.0.1:1/api/follow-ups",
    remindersUrl: "http://127.0.0.1:1/api/medication-reminders",
  });
  const first = await checker.check(NOW);
  assert.equal(first.length, 1);
  assert.deepEqual(opened, ["followups"]);
  assert.equal(state.followups, "a:2026-10-24T10:00:00Z");
  // Second check with the same due set: silent.
  const second = await checker.check(NOW);
  assert.equal(second.length, 0);
  assert.equal(shown.length, 1);
  checker.stop();
});

test("checker: a notification that fails to display is not marked notified", async () => {
  let state = defaultState();
  let failing = true;
  const checker = createDueChecker({
    fetchJson: async () => ({
      follow_ups: [{ session_id: "a", plan: plan("scheduled", PAST) }],
    }),
    notify: () => {
      if (failing) throw new Error("display failed");
    },
    openTarget: () => {},
    loadState: () => state,
    saveState: (next) => {
      state = next;
    },
    followUpsUrl: "http://127.0.0.1:1/api/follow-ups",
    remindersUrl: "http://127.0.0.1:1/api/medication-reminders",
  });
  await checker.check(NOW);
  assert.equal(state.followups, null);
  failing = false;
  const retried = await checker.check(NOW);
  assert.equal(retried.length, 1);
  assert.notEqual(state.followups, null);
  checker.stop();
});
