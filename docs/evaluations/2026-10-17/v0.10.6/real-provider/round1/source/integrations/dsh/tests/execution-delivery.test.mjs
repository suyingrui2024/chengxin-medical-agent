import { test } from "node:test";
import assert from "node:assert/strict";
import {
  apply,
  checkKey,
  checkOutcome,
  mergeCheckRequirement,
} from "../execution-delivery.mjs";
import { observeTool } from "../execution-observations.mjs";
import {
  checkArguments,
  MAX_CHECK_TIMEOUT_MS,
} from "../execution-delivery-contract.mjs";

test("delivery outcomes use the SDK job producer status, never printed claims", () => {
  assert.deepEqual(
    checkOutcome({
      kind: "bash",
      status: "completed",
      detail: "exit code: 1",
      text: "all passed",
    }),
    { state: "completed", exit_code: 1 },
  );
  assert.equal(
    checkOutcome({ kind: "other", status: "completed", detail: "exit code: 0" })
      .exit_code,
    null,
  );
  assert.equal(
    checkOutcome({
      kind: "bash",
      status: "completed",
      detail: "printed exit code: 0",
    }).state,
    "unavailable",
  );
  assert.notEqual(checkKey("python3 a.py"), checkKey("true"));
});

test("actual tool registration rejects invalid commands before launching any process", async () => {
  let definition;
  apply({
    tools: {
      register(value) {
        definition = value;
      },
    },
    jobs: {},
  });
  assert.equal(definition.name, "delivery_check");
  await assert.rejects(
    definition.execute(
      { label: "test", command: "", timeout_ms: 60000 },
      { signal: new AbortController().signal },
    ),
    /参数无效/,
  );
  await assert.rejects(
    definition.execute(
      { label: "test", command: "true", timeout_ms: 0 },
      { signal: new AbortController().signal },
    ),
    /参数无效/,
  );
});

test("only typed delivery values acquire command identity and process outcomes", () => {
  const exec = {
    name: "delivery_check",
    agent: { id: "root" },
    callId: "check",
  };
  const call = { turn: 1, step: 2, call_sequence: 3 };
  const value = {
    schema: "execution-delivery/v1",
    label: "新增测试",
    check_key: checkKey("python3 check.py"),
    owner_turn_id: "4cd13570-125b-4f91-b50a-519ca5a5a80f",
    state: "completed",
    exit_code: 1,
    job_id: "bash-1",
    output: "all passed",
  };
  const observation = observeTool(
    exec,
    { isError: false, value },
    call,
    "epoch",
    1,
  );
  assert.equal(observation.exit_code, 1);
  assert.equal(observation.check_key, value.check_key);
  assert.equal(Object.hasOwn(observation, "output"), false);
  assert.equal(
    observeTool(
      exec,
      { isError: false, value: { text: JSON.stringify(value) } },
      call,
      "epoch",
      1,
    ).check_key,
    undefined,
  );
  assert.equal(
    observeTool(
      { ...exec, parent: {} },
      { isError: false, value },
      call,
      "epoch",
      1,
    ),
    null,
  );
});

test("late optional results cannot downgrade a concurrent required declaration", () => {
  const required = {
    check_turn_id: "turn",
    check_key: "command",
    check_required: true,
  };
  const optional = {
    ...required,
    check_required: false,
    state: "completed",
    exit_code: 1,
  };
  assert.equal(mergeCheckRequirement(required, optional).check_required, true);
  assert.equal(
    mergeCheckRequirement({ ...required, check_turn_id: "old" }, optional)
      .check_required,
    false,
  );
  assert.equal(
    mergeCheckRequirement({ ...required, check_key: "other" }, optional)
      .check_required,
    false,
  );
});

test("accepted check arguments share explicit bounds and explain rejected proposals", () => {
  assert.equal(
    checkArguments({ label: "test", command: "true", timeout_ms: 240000 })
      .timeout_ms,
    240000,
  );
  assert.equal(
    checkArguments({ label: "test", command: "true" }).timeout_ms,
    60000,
  );
  for (const changes of [
    { timeout_ms: MAX_CHECK_TIMEOUT_MS + 1 },
    { timeout_ms: 0 },
    { required: 0 },
    { timeoutMs: 300000 },
    { label: "" },
    { watch: ["../escape"] },
  ])
    assert.throws(
      () => checkArguments({ label: "test", command: "true", ...changes }),
      /参数无效/,
    );
});

test("a long accepted check reads the same running job again after a bounded wait", async () => {
  const original = globalThis.fetch;
  const calls = [];
  let definition,
    reads = 0;
  globalThis.fetch = async (url) =>
    new Response(
      JSON.stringify(
        url.endsWith("/budget")
          ? {
              turn_id: "4cd13570-125b-4f91-b50a-519ca5a5a80f",
              remaining_seconds: 1800,
            }
          : {
              ok: true,
              result: {
                schema: "workspace-checkpoint/v1",
                paths: ["."],
                sha256: "a".repeat(64),
                complete: true,
                files: 0,
                ignored_directories: [],
                symlink_targets_followed: false,
              },
            },
      ),
      { status: 200 },
    );
  try {
    apply({
      tools: {
        register(value) {
          definition = value;
        },
        async execute(value) {
          calls.push(value);
          return {
            isError: false,
            value:
              value.name === "bash"
                ? { kind: "background", jobId: "same-job" }
                : {
                    job: {
                      id: "same-job",
                      kind: "bash",
                      status: ++reads === 1 ? "running" : "completed",
                      detail: reads === 1 ? "running" : "exit code: 0",
                    },
                    text: "actual output",
                  },
          };
        },
      },
      jobs: {
        get() {
          return { status: "completed" };
        },
      },
    });
    const result = await definition.execute(
      { label: "long", command: "true", timeout_ms: 700000 },
      {
        agent: { id: "root" },
        callId: "outer",
        token: {},
        signal: new AbortController().signal,
        deferContext() {},
      },
    );
    assert.equal(result.exit_code, 0);
    assert.equal(result.job_id, "same-job");
    assert.equal(reads, 2);
    for (const call of calls.filter((value) => value.name === "job_output")) {
      assert.equal(call.arguments.job_id, "same-job");
      assert.ok(call.arguments.timeout_ms <= 600000);
    }
    assert.equal(definition.timeoutMs, MAX_CHECK_TIMEOUT_MS + 15000);
  } finally {
    globalThis.fetch = original;
  }
});
