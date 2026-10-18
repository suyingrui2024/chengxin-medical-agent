import { test } from "node:test";
import assert from "node:assert/strict";
import { actualInputCheck } from "../../../tests/fixtures/delivery-inputs.mjs";
import {
  coversVerifiedInputs,
  validInputScope,
} from "../execution-checkpoints.mjs";
import { observeTool } from "../execution-observations.mjs";
import { apply } from "../execution-delivery.mjs";
import { createHash } from "node:crypto";
import { checkArguments } from "../execution-delivery-contract.mjs";

test("actual exit zero and matching final snapshot do not verify input modified during checking", async () => {
  const value = await actualInputCheck({ strict: true, mutate: true });
  assert.equal(value.exit, 0);
  assert.equal(value.receipt.exit_code, 0);
  assert.equal(value.finalSource, "bad");
  assert(value.inputChanged);
  assert.equal(value.receipt.checkpoint.sha256, value.finalSnapshot.sha256);
  assert.deepEqual(value.receipt.inputs_verified, []);
  assert.equal(
    coversVerifiedInputs(value.receipt.inputs_verified, ["source.txt"]),
    false,
  );
  assert.match(value.receipt.output, /输入在运行期间变化/);
  assert.equal(value.requests.length, 3);
});
test("stable explicit inputs permit generated outputs and keep the post-run file binding", async () => {
  const value = await actualInputCheck({ strict: true, mutate: false });
  assert.equal(value.exit, 0);
  assert.equal(value.finalSource, "good");
  assert.equal(value.inputChanged, false);
  assert.equal(value.report, "generated output");
  assert.deepEqual(value.receipt.inputs_verified, ["source.txt"]);
  assert.equal(
    coversVerifiedInputs(value.receipt.inputs_verified, ["source.txt"]),
    true,
  );
  assert.equal(value.receipt.checkpoint.sha256, value.finalSnapshot.sha256);
  assert.deepEqual(value.requests, [["source.txt"], ["."], ["source.txt"]]);
});
test("post-run-only checks retain their documented scope and cannot satisfy a later input requirement", async () => {
  const value = await actualInputCheck({ strict: false, mutate: true });
  assert.equal(value.exit, 0);
  assert.equal(value.receipt.checkpoint.sha256, value.finalSnapshot.sha256);
  assert.equal(value.receipt.inputs_verified, undefined);
  assert.equal(coversVerifiedInputs(value.receipt.inputs_verified, []), true);
  assert.equal(
    coversVerifiedInputs(value.receipt.inputs_verified, ["source.txt"]),
    false,
  );
  assert.equal(value.requests.length, 1);
});
test("input proof scopes must be canonical, explicit and cover the declared dependencies", () => {
  assert.deepEqual(
    checkArguments({
      label: "test",
      command: "true",
      inputs: ["src/a.py", "src"],
    }).inputs,
    ["src"],
  );
  for (const inputs of [
    [],
    ["../escape"],
    ["C:\\host"],
    [".git/../secret"],
    "src",
    Array(17).fill("src"),
  ])
    assert.throws(
      () => checkArguments({ label: "test", command: "true", inputs }),
      /参数无效/,
    );
  for (const proof of [
    null,
    undefined,
    {},
    ["../escape"],
    ["src/a.py", "src"],
    ["src", "src"],
  ])
    assert.equal(validInputScope(proof), false);
  assert.equal(coversVerifiedInputs(["src"], ["src/a.py"]), true);
  assert.equal(coversVerifiedInputs(["src/a.py"], ["src"]), false);
  assert.equal(coversVerifiedInputs(["."], ["node_modules/pkg"]), false);
});

test("only the typed delivery producer can attach verified input scope", () => {
  const exec = {
    name: "delivery_check",
    agent: { id: "root" },
    callId: "inputs",
  };
  const call = { turn: 1, step: 1, call_sequence: 1 };
  const value = {
    schema: "execution-delivery/v1",
    label: "Input check",
    check_key: "a".repeat(64),
    owner_turn_id: "4cd13570-125b-4f91-b50a-519ca5a5a80f",
    state: "completed",
    exit_code: 0,
    inputs_verified: ["src"],
  };
  assert.deepEqual(
    observeTool(exec, { value }, call, "epoch", 1).check_inputs,
    ["src"],
  );
  assert.deepEqual(
    observeTool(
      exec,
      { value: { ...value, inputs_verified: ["../escape"] } },
      call,
      "epoch",
      1,
    ).check_inputs,
    [],
  );
  assert.equal(
    observeTool(
      exec,
      { value: { text: JSON.stringify(value) } },
      call,
      "epoch",
      1,
    ).check_inputs,
    undefined,
  );
});

for (const unavailableAtStart of [false, true]) {
  test(`input requirements survive later omission and expansion; missing start proof=${unavailableAtStart}`, async () => {
    const original = globalThis.fetch;
    let definition,
      reads = 0;
    globalThis.fetch = async (url, options) => {
      if (url.endsWith("/budget"))
        return Response.json({
          turn_id: "4cd13570-125b-4f91-b50a-519ca5a5a80f",
          remaining_seconds: 60,
        });
      if (unavailableAtStart && !reads++)
        return Response.json({ ok: false }, { status: 503 });
      const paths = JSON.parse(options.body).paths;
      return Response.json({
        ok: true,
        result: {
          schema: "workspace-checkpoint/v1",
          paths,
          sha256: createHash("sha256")
            .update(JSON.stringify(paths))
            .digest("hex"),
          complete: true,
          files: 1,
          ignored_directories: [],
          symlink_targets_followed: false,
        },
      });
    };
    try {
      apply({
        tools: {
          register(value) {
            definition = value;
          },
          async execute(call) {
            return {
              isError: false,
              value:
                call.name === "bash"
                  ? { kind: "background", jobId: "same-job" }
                  : {
                      job: {
                        kind: "bash",
                        status: "completed",
                        detail: "exit code: 0",
                      },
                      text: "actual job producer outcome",
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
      const execute = (inputs) =>
        definition.execute(
          {
            label: "Scope contract",
            command: "true",
            ...(inputs ? { inputs } : {}),
          },
          {
            agent: { id: "root" },
            callId: "scope",
            token: {},
            signal: new AbortController().signal,
            deferContext() {},
          },
        );
      const first = await execute(["src"]);
      assert.deepEqual(
        first.inputs_verified,
        unavailableAtStart ? [] : ["src"],
      );
      assert(first.checkpoint.complete);
      const expanded = await execute(["tests"]);
      assert.deepEqual(expanded.inputs_verified, ["src", "tests"]);
      const omitted = await execute();
      assert.deepEqual(omitted.inputs_verified, ["src", "tests"]);
      assert.equal(coversVerifiedInputs(["src"], ["src", "tests"]), false);
    } finally {
      globalThis.fetch = original;
    }
  });
}
