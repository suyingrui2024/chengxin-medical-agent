import test from "node:test";
import assert from "node:assert/strict";
import { taskBoundSpec } from "../execution-deadline.mjs";

const owner = "11111111-1111-4111-8111-111111111111";
function fixture(t, budget, ok = true) {
  t.mock.method(Date, "now", () => 100000);
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    assert.equal(options.redirect, "error");
    assert.ok(options.signal instanceof AbortSignal);
    return { ok, json: async () => budget };
  });
}

test("a promoted or background process keeps the captured task deadline without changing its waiting spec", async (t) => {
  const budget = { turn_id: owner, deadline_at_ms: 140000 };
  fixture(t, budget);
  const original = { command: "synthetic", timeoutMs: 10000, onExpiry: "none" };
  const pinned = await taskBoundSpec(original);
  assert.equal(pinned.timeoutMs, 40000);
  assert.equal(pinned.onExpiry, "kill");
  assert.equal(original.onExpiry, "none");
  assert.equal(original.timeoutMs, 10000);
  budget.deadline_at_ms = 200000; // A later task grant cannot change this process.
  assert.equal(pinned.timeoutMs, 40000);
});

test("an existing foreground kill deadline never grows beyond either bound", async (t) => {
  fixture(t, { turn_id: owner, deadline_at_ms: 140000 });
  const short = await taskBoundSpec({ timeoutMs: 10000, onExpiry: "kill" });
  const long = await taskBoundSpec({ timeoutMs: 60000, onExpiry: "kill" });
  assert.equal(short.timeoutMs, 10000);
  assert.equal(long.timeoutMs, 40000);
});

test("missing, expired, excessive or malformed grants cannot launch an unbounded command", async (t) => {
  fixture(t, {});
  for (const budget of [
    null,
    {},
    { turn_id: owner, deadline_at_ms: null },
    { turn_id: owner, deadline_at_ms: 100000 },
    { turn_id: owner, deadline_at_ms: 1900001 },
    { turn_id: owner, deadline_at_ms: "140000" },
    { turn_id: "other", deadline_at_ms: 140000 },
  ]) {
    t.mock.method(globalThis, "fetch", async () => ({
      ok: true,
      json: async () => budget,
    }));
    await assert.rejects(
      taskBoundSpec({ timeoutMs: 10000, onExpiry: "none" }),
      /有效的任务时限/,
    );
  }
});

test("a failed host read cannot replace a process deadline with the command wait limit", async (t) => {
  fixture(t, { turn_id: owner, deadline_at_ms: 140000 }, false);
  await assert.rejects(
    taskBoundSpec({ timeoutMs: 10000, onExpiry: "none" }),
    /有效的任务时限/,
  );
});
