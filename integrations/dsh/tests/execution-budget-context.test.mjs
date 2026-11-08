import test from "node:test";
import assert from "node:assert/strict";
import { registerBudgetContext } from "../execution-budget-context.mjs";

test("the budget hint is queried after admission with the actual agent identity", async (t) => {
  let hook,
    admitted = false;
  registerBudgetContext({
    on(name, callback) {
      assert.equal(name, "agent/pre-step");
      hook = callback;
    },
  });
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    assert.equal(admitted, true);
    assert.equal(options.headers["x-chengxin-session-id"], "current-agent");
    assert.equal(options.redirect, "error");
    return {
      ok: true,
      json: async () => ({ remaining_calls: 4, remaining_seconds: 599 }),
    };
  });
  const user = { id: "real-user" };
  const result = await hook(
    { agent: { id: "current-agent" }, signal: new AbortController().signal },
    async () => {
      admitted = true;
      return { kind: "enter", messages: [user] };
    },
  );
  assert.equal(result.messages[0], user);
  assert.match(result.messages[1].content[0].text, /剩余执行时间约 599 秒/);
  assert.equal(result.messages[1].source.kind, "chengxin-execution-budget");
});

test("rejected steps never query the budget; transport failures do not invent a budget", async (t) => {
  let hook;
  registerBudgetContext({
    on(_name, callback) {
      hook = callback;
    },
  });
  const fetch = t.mock.method(globalThis, "fetch", async () => {
    throw Error("fixture unavailable");
  });
  const payload = {
    agent: { id: "root" },
    signal: new AbortController().signal,
  };
  const reject = { kind: "reject" };
  assert.equal(await hook(payload, async () => reject), reject);
  assert.equal(fetch.mock.callCount(), 0);
  const decision = { kind: "enter", messages: [] };
  assert.equal(await hook(payload, async () => decision), decision);
  assert.equal(fetch.mock.callCount(), 1);
});
