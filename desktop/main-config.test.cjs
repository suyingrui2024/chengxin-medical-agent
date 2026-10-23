const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

// main.cjs requires Electron and cannot be loaded by node --test, so the
// startup wiring that has silently regressed before is pinned here as
// static assertions on the source.
const source = fs.readFileSync(path.join(__dirname, "main.cjs"), "utf8");

test("the window session partition persists across restarts", () => {
  // Without persist:, localStorage (font size, web appearance) is discarded
  // at exit while the settings page promises it is kept for the next launch.
  assert.match(source, /fromPartition\("persist:chengxin-desktop"\)/);
  assert.doesNotMatch(source, /fromPartition\("chengxin-desktop"\)/);
});

test("userData is not versioned and legacy appearance migration is wired", () => {
  assert.match(
    source,
    /path\.join\(app\.getPath\("appData"\), "chengxin-medagent"\)/,
  );
  assert.doesNotMatch(source, /chengxin-medagent-\$\{/);
  assert.match(source, /migrateLegacyAppearance\(/);
});

test("backend startup failures carry distinct kinds", () => {
  for (const kind of [
    "interpreter_missing",
    "spawn_error",
    "exited_before_ready",
    "ready_timeout",
  ])
    assert.ok(source.includes(`"${kind}"`), `missing startup kind ${kind}`);
  assert.match(source, /startupFailureGuidance\(error\)/);
});
