import { test } from "node:test";
import assert from "node:assert/strict";

test("importing application modules does not query the DOM or bind feature events", async () => {
  // Node deliberately provides neither window nor document. Import-time
  // wiring used to fail here; boot() is now the explicit composition root.
  const bootstrap = await import("../../src/medagent/web/js/app/bootstrap.js");
  const context = await import("../../src/medagent/web/js/app/context.js");
  assert.equal(typeof bootstrap.boot, "function");
  assert.equal(context.structuredUI, undefined);
  assert.equal(context.executionUI, undefined);
  assert.equal(context.patientRecordsUI, undefined);
  assert.equal(
    context.displayFact("trajectory", {
      assertion: "present",
      value: "intermittent",
    }),
    "阵发性",
  );
});
