import { test } from "node:test";
import assert from "node:assert/strict";
import {
  inputProofMissing,
  observationPresentation,
} from "../../src/medagent/web/execution-observation-ui.js";

test("input proof and command exit status remain distinct in presentation", () => {
  const value = {
    schema: "execution-observation/v1",
    tool: "delivery_check",
    state: "completed",
    exit_code: 0,
    check_label: "Input check",
  };
  assert.equal(inputProofMissing(value), false);
  assert.equal(observationPresentation(value).kind, "result");
  assert.equal(
    observationPresentation({ ...value, check_inputs: ["src"] }).kind,
    "result",
  );
  const missing = { ...value, check_inputs: [] };
  assert.equal(inputProofMissing(missing), true);
  assert.equal(observationPresentation(missing).kind, "failure");
  assert.match(observationPresentation(missing).title, /输入待复验/);
  assert.match(observationPresentation(missing).title, /退出 0/);
});
