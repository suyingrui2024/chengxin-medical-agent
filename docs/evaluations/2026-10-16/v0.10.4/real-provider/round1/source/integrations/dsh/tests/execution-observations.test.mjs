import { test } from "node:test";
import assert from "node:assert/strict";
import { observeTool, hasActiveJobs } from "../execution-observations.mjs";
import { observationPresentation } from "../../../src/medagent/web/execution-observation-ui.js";

const exec = { name: "bash", agent: { id: "root" }, callId: "call" };
const call = { turn: 1, step: 2, call_sequence: 3 };
const observe = (execution, value) =>
  observeTool(execution, { value }, call, "epoch", 1);

test("stdout cannot override a canonical process outcome", () => {
  const value = observe(exec, {
    kind: "foreground",
    exitCode: 0,
    stdout: "[exit code: 7]",
  });
  assert.equal(value.exit_code, 0);
  assert.equal(observationPresentation(value).title, "命令退出 · 0");
  assert.equal(
    observationPresentation(
      observe(exec, { kind: "foreground", exitCode: 7, stdout: "success" }),
    ).kind,
    "failure",
  );
  assert.equal(
    observationPresentation(observe(exec, { text: "[exit code: 0]" })).kind,
    "model",
  );
  assert.equal(Object.hasOwn(value, "stdout"), false);
});

test("background process detail is separate from output and producer kinds", () => {
  const job = {
    id: "bash-1",
    kind: "bash",
    status: "completed",
    detail: "exit code: 9; [sandbox note]",
  };
  const execution = { ...exec, name: "job_output" };
  assert.equal(
    observe(execution, { job, text: "[exit code: 0]" }).exit_code,
    9,
  );
  assert.equal(
    observe(execution, { job: { ...job, kind: "subagent" } }).exit_code,
    null,
  );
  assert.equal(
    observe(execution, { job: { ...job, detail: "arbitrary exit code: 9" } })
      .exit_code,
    null,
  );
  assert.equal(
    observe(execution, {
      job: { ...job, status: "killed", detail: "signal: SIGTERM" },
    }).signal,
    "SIGTERM",
  );
});

test("nested calls and missing call coordinates do not acquire invented identity", () => {
  assert.equal(observe({ ...exec, parent: {} }, {}), null);
  assert.equal(observeTool(exec, {}, null, "epoch", 1), null);
  const first = observe(exec, { kind: "foreground", exitCode: 0 });
  const next = observeTool(
    exec,
    { value: { kind: "foreground", exitCode: 0 } },
    { ...call, turn: 2 },
    "epoch",
    2,
  );
  assert.notEqual(first.sdk_turn, next.sdk_turn);
  assert.notEqual(first.sequence, next.sequence);
});

test("active preparation and stopping work cannot be treated as quiescent", () => {
  assert.equal(hasActiveJobs([{ status: "starting" }]), true);
  assert.equal(hasActiveJobs([{ status: "running" }]), true);
  assert.equal(hasActiveJobs([{ status: "stopping" }]), true);
  assert.equal(
    hasActiveJobs([{ status: "completed" }, { status: "failed" }]),
    false,
  );
});
