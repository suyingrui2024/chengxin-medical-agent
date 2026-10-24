const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

// main.cjs requires Electron and cannot be loaded by node --test, so the
// due-center wiring is pinned here as static assertions on the sources,
// following the main-config.test.cjs convention.
const main = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");
const preload = fs.readFileSync(path.join(__dirname, "preload.cjs"), "utf8");
const app = fs.readFileSync(
  path.join(__dirname, "..", "src", "medagent", "web", "app.js"),
  "utf8",
);

test("main process polls follow-ups and reminders with the backend token", () => {
  assert.match(main, /require\("\.\/due-notifications\.cjs"\)/);
  assert.match(main, /createDueChecker\(/);
  assert.match(main, /\/api\/follow-ups/);
  assert.match(main, /\/api\/medication-reminders/);
  assert.match(main, /"X-MedAgent-Token": token/);
  assert.match(main, /dueChecker\.start\(\)/);
});

test("main process shows a system notification whose click routes to the renderer", () => {
  assert.match(main, /new Notification\(\{/);
  assert.match(main, /notification\.on\("click", onClick\)/);
  assert.match(main, /webContents\.send\("due-notifications:open"/);
  assert.match(preload, /dueNotifications: \{/);
  assert.match(preload, /ipcRenderer\.on\("due-notifications:open"/);
  assert.match(app, /dueNotifications\?\.onOpen/);
  assert.match(app, /navigate\("history"\)/);
});

test("web badge aggregates follow-ups and medication reminders by one due rule", () => {
  assert.match(app, /dueReminderCount\(\)/);
  assert.match(app, /const total = due \+ dueMeds/);
  assert.match(app, /badge\.textContent = String\(total\)/);
});

test("the app-closed boundary is stated on the follow-up page", () => {
  assert.match(app, /followup-boundary-note/);
  assert.match(app, /仅在应用打开时送达/);
  assert.match(app, /关闭应用后不会推送/);
});
