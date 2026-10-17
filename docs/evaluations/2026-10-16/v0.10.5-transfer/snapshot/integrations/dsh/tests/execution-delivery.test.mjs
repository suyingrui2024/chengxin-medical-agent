import { test } from "node:test";
import assert from "node:assert/strict";
import {
  apply,
  checkKey,
  checkOutcome,
  mergeCheckRequirement,
} from "../execution-delivery.mjs";
import { observeTool } from "../execution-observations.mjs";

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
