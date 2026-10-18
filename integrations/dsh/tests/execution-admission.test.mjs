import test from "node:test";
import assert from "node:assert/strict";
import { RootAdmission } from "../execution-admission.mjs";

const oldTask = "11111111-1111-4111-8111-111111111111";
const newTask = "22222222-2222-4222-8222-222222222222";

function message(id, kind, extra = {}) {
  return { id, content: [{ type: "text", text: id }], source: { kind, ...extra } };
}

function fixture() {
  const admitted = [];
  const deferred = [];
  const admission = new RootAdmission({
    resolveOwner: async (session) => (session === "old-child" ? oldTask : null),
    admit: async (value) => {
      admitted.push(value);
      return true;
    },
  });
  const agent = { id: "root", followup: (value) => deferred.push(value.id) };
  return { admission, admitted, deferred, agent };
}

test("a registered root prompt receives its own task before the model boundary", async () => {
  const { admission, admitted, agent } = fixture();
  admission.register(newTask);
  const user = message("new-user", "user");
  admission.inserted(agent, user);
  const decision = await admission.decide(
    { agent, messages: [user], turn: 7, signal: new AbortController().signal },
    async () => ({ kind: "enter", messages: [user, { id: "runtime" }] }),
  );
  assert.equal(admitted.length, 1);
  assert.deepEqual(
    { ...admitted[0], signal: undefined },
    { session_id: "root", sdk_turn: 7, task_id: newTask, signal: undefined },
  );
  assert.deepEqual(decision.messages.map((value) => value.id), ["new-user", "runtime"]);
});

test("an old child settlement is deferred when a newer user prompt is claimed with it", async () => {
  const { admission, admitted, deferred, agent } = fixture();
  admission.register(newTask);
  const user = message("new-user", "user");
  const settled = message("old-settled", "subagent-settled", { senderSessionId: "old-child" });
  admission.inserted(agent, user);
  admission.inserted(agent, settled);
  const decision = await admission.decide(
    { agent, messages: [settled, user], turn: 8, signal: new AbortController().signal },
    async () => ({ kind: "enter", messages: [settled, user] }),
  );
  assert.equal(admitted[0].task_id, newTask);
  assert.deepEqual(deferred, ["old-settled"]);
  assert.deepEqual(decision.messages.map((value) => value.id), ["new-user"]);
});

test("a later child settlement uses the child task after the foreground prompt ended", async () => {
  const { admission, admitted, agent } = fixture();
  admission.root = agent.id;
  const settled = message("old-settled", "subagent-settled", { senderSessionId: "old-child" });
  admission.inserted(agent, settled);
  const decision = await admission.decide(
    { agent, messages: [settled], turn: 9, signal: new AbortController().signal },
    async () => ({ kind: "enter", messages: [settled] }),
  );
  assert.equal(admitted[0].task_id, oldTask);
  assert.deepEqual(decision.messages.map((value) => value.id), ["old-settled"]);
});

test("an unattributed root notice is rejected before a provider request", async () => {
  const { admission, admitted, agent } = fixture();
  admission.root = agent.id;
  const unknown = message("unknown", "tool-jobs");
  admission.inserted(agent, unknown);
  const decision = await admission.decide(
    { agent, messages: [unknown], turn: 10, signal: new AbortController().signal },
    async () => ({ kind: "enter", messages: [unknown] }),
  );
  assert.deepEqual(decision, { kind: "reject" });
  assert.deepEqual(admitted, []);
});

test("a later tool-only step keeps the root turn admitted by its first message", async () => {
  const { admission, admitted, agent } = fixture();
  admission.root = agent.id;
  const entered = await admission.decide(
    { agent, messages: [], turn: 11, signal: new AbortController().signal },
    async () => ({ kind: "enter", messages: [] }),
  );
  assert.deepEqual(entered, { kind: "enter", messages: [] });
  assert.deepEqual(admitted, []);
});
