import { test } from "node:test";
import assert from "node:assert/strict";
import { apply } from "../execution-workspace.mjs";

test("the pinned SDK accepts and registers the real workspace tool definition", () => {
  const registered = [];
  apply({ tools: { register: (definition) => registered.push(definition) } });
  assert.equal(registered.length, 1);
  assert.equal(registered[0].name, "workspace_changes");
  assert.equal(typeof registered[0].execute, "function");
});
