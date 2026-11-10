import test from "node:test";
import assert from "node:assert/strict";
import {
  apply,
  SubagentCompletions,
  waitArguments,
} from "../execution-subagents.mjs";

const signal = () => new AbortController().signal;
const activation = (id = "child", runId = "run") => ({ id, runId });
const completed = (id = "child", runId = "run", text = "verified") => ({
  ...activation(id, runId),
  stopReason: "completed",
  lastAssistantMessage: [{ type: "text", text }],
});

test("settlement before or during a wait returns the actual child output", async () => {
  const tracker = new SubagentCompletions(),
    parent = {};
  tracker.started(parent, activation());
  const pending = tracker.wait(parent, "child", 1000, signal());
  let resolved = false;
  pending.then(() => (resolved = true));
  await Promise.resolve();
  assert.equal(resolved, false);
  tracker.settled(parent, completed());
  const result = await pending;
  assert.equal(result.state, "completed");
  assert.equal(result.output, "verified");
  assert.deepEqual(await tracker.wait(parent, "child", 1, signal()), result);
});

test("the exact delegating parent owns each child's result", async () => {
  const tracker = new SubagentCompletions(),
    parent = {},
    other = {};
  tracker.started(parent, activation());
  tracker.started(other, activation("sibling", "other-run"));
  assert.throws(() => tracker.wait(other, "child", 1, signal()), /直接子任务/);
  assert.throws(
    () => tracker.wait({ ...parent }, "child", 1, signal()),
    /直接子任务/,
  );
  tracker.settled(other, completed());
  assert.equal(
    (await tracker.wait(parent, "child", 1, signal())).state,
    "timed_out",
  );
});

test("a timeout leaves the child running and the same activation can be waited again", async () => {
  const tracker = new SubagentCompletions(),
    parent = {};
  tracker.started(parent, activation());
  const timed = await tracker.wait(parent, "child", 1, signal());
  assert.equal(timed.state, "timed_out");
  assert.equal(Object.hasOwn(timed, "output"), false);
  tracker.settled(parent, completed());
  assert.equal(
    (await tracker.wait(parent, "child", 1, signal())).output,
    "verified",
  );
});

test("a continued activation cannot return its old result or a late old settlement", async () => {
  const tracker = new SubagentCompletions(),
    parent = {};
  tracker.started(parent, activation());
  tracker.settled(parent, completed());
  tracker.started(parent, activation("child", "follow-up"));
  tracker.settled(parent, completed("child", "run", "stale"));
  assert.equal(
    (await tracker.wait(parent, "child", 1, signal())).state,
    "timed_out",
  );
  tracker.settled(parent, completed("child", "follow-up", "revised"));
  const result = await tracker.wait(parent, "child", 1, signal());
  assert.equal(result.activation_id, "follow-up");
  assert.equal(result.output, "revised");
});

test("cancelling one wait cleans up without cancelling another parent's work", async () => {
  const tracker = new SubagentCompletions(),
    parent = {},
    controller = new AbortController();
  tracker.started(parent, activation());
  const pending = tracker.wait(parent, "child", 10000, controller.signal);
  const rejected = assert.rejects(pending, { name: "AbortError" });
  controller.abort();
  await rejected;
  assert.throws(() => tracker.wait(parent, "child", 10000, controller.signal), {
    name: "AbortError",
  });
  tracker.settled(parent, completed());
  assert.equal(
    (await tracker.wait(parent, "child", 1, signal())).state,
    "completed",
  );
});

test("failed, cancelled and empty children never acquire a fabricated success answer", async () => {
  for (const [reason, state] of [
    ["error", "failed"],
    ["max-tokens", "failed"],
    ["refusal", "failed"],
    ["aborted", "cancelled"],
    ["completed", "completed"],
  ]) {
    const tracker = new SubagentCompletions(),
      parent = {};
    tracker.started(parent, activation());
    tracker.settled(parent, { ...activation(), stopReason: reason });
    const result = await tracker.wait(parent, "child", 1, signal());
    assert.equal(result.state, state);
    assert.equal(result.stop_reason, reason);
    assert.equal(result.output, "");
  }
});

test("large text is bounded and marked; non-text blocks are never executed", async () => {
  const tracker = new SubagentCompletions(),
    parent = {};
  tracker.started(parent, activation());
  tracker.settled(parent, {
    ...completed("child", "run", "x".repeat(20000)),
    lastAssistantMessage: [
      { type: "tool_use", name: "bash", input: {} },
      { type: "text", text: "x".repeat(20000) },
    ],
  });
  const result = await tracker.wait(parent, "child", 1, signal());
  assert.equal(result.output.length, 16384);
  assert.equal(result.output_truncated, true);
});

test("tool arguments and live caller identity are checked before waiting", async () => {
  for (const args of [
    { agent_id: "" },
    { agent_id: 2 },
    ...[0, -1, 1.5, 600001, "100"].map((timeout_ms) => ({
      agent_id: "child",
      timeout_ms,
    })),
  ])
    assert.throws(() => waitArguments(args), /agent_id/);
  assert.deepEqual(waitArguments({ agent_id: "child" }), {
    id: "child",
    timeout: 30000,
  });
  let definition;
  apply({
    on() {},
    agents: { get() {} },
    tools: {
      register(value) {
        definition = value;
      },
    },
  });
  assert.equal(definition.name, "wait_subagent");
  assert.equal(definition.isConcurrencySafe(), false);
  await assert.rejects(
    () =>
      definition.execute(
        { agent_id: "child" },
        { agent: { id: "root" }, signal: signal() },
      ),
    /真实运行身份/,
  );
});

test("bounded history never evicts a pending child to retain old completed results", async () => {
  const tracker = new SubagentCompletions(),
    parent = {};
  tracker.started(parent, activation());
  for (let i = 0; i < 260; i++) {
    tracker.started(parent, activation(String(i), String(i)));
    tracker.settled(parent, completed(String(i), String(i)));
  }
  assert.throws(() => tracker.wait(parent, "0", 1, signal()), /本运行未观测/);
  tracker.settled(parent, completed());
  assert.equal(
    (await tracker.wait(parent, "child", 1, signal())).output,
    "verified",
  );
});
