import test from "node:test";
import assert from "node:assert/strict";
import { cancelRuntime } from "../execution-cancellation.mjs";
import {
  registerControl,
  dispatchControl,
} from "../execution-control-channel.mjs";

function fixture() {
  const cancelled = [],
    killed = [],
    waited = [];
  const agents = ["root", "old-child", "new-child"].map((id) => ({
    id,
    status: id === "root" ? "idle" : "running",
    cancel: () => cancelled.push(id),
    whenIdle: async () => waited.push(id),
  }));
  const jobs = new Map([
    [
      "root",
      [
        { id: "old-command", status: "running" },
        { id: "new-command", status: "running" },
      ],
    ],
    ["old-child", [{ id: "child-command", status: "starting" }]],
    ["new-child", [{ id: "new-child-command", status: "running" }]],
  ]);
  return {
    cancelled,
    killed,
    waited,
    agents,
    ctx: {
      agents: { list: () => agents },
      jobs: {
        list: (id) => jobs.get(id),
        kill: (id, owner) => killed.push([id, owner]),
        wait: async () => {},
      },
    },
  };
}
test("an expiring grant stops only its child and recorded root command", async () => {
  const f = fixture();
  assert.equal(
    (
      await cancelRuntime(f.ctx, {
        root: "root",
        sessions: ["old-child"],
        jobs: [{ id: "old-command", session_id: "root" }],
      })
    ).status,
    200,
  );
  assert.deepEqual(f.cancelled, ["old-child"]);
  assert.deepEqual(f.killed, [
    ["old-command", "root"],
    ["child-command", "old-child"],
  ]);
  assert.deepEqual(f.waited, ["old-child"]);
});
test("stopping the current root does not kill another grant's background root job", async () => {
  const f = fixture();
  f.agents[0].status = "running";
  await cancelRuntime(f.ctx, {
    root: "root",
    sessions: ["root", "new-child"],
    jobs: [{ id: "new-command", session_id: "root" }],
  });
  assert.deepEqual(f.cancelled, ["root", "new-child"]);
  assert.deepEqual(f.killed, [
    ["new-command", "root"],
    ["new-child-command", "new-child"],
  ]);
});
test("an explicit empty scope has no global effect and idle inboxes remain intact", async () => {
  const f = fixture();
  await cancelRuntime(f.ctx, { root: "root", sessions: ["root"], jobs: [] });
  assert.deepEqual(f.cancelled, []);
  assert.deepEqual(f.killed, []);
  await cancelRuntime(f.ctx, { root: "root", sessions: [], jobs: [] });
  assert.deepEqual(f.cancelled, []);
  assert.deepEqual(f.killed, []);
});
test("an unscoped user stop still cancels every agent and active job", async () => {
  const f = fixture();
  await cancelRuntime(f.ctx);
  assert.deepEqual(f.cancelled, ["root", "old-child", "new-child"]);
  assert.equal(f.killed.length, 4);
});
test("malformed scopes cannot become a workspace stop", async () => {
  const f = fixture();
  for (const scope of [
    null,
    {},
    [],
    { root: "root", sessions: [], jobs: [{ id: "x" }] },
    { root: "root", sessions: ["bad\n"], jobs: [] },
  ])
    assert.equal((await cancelRuntime(f.ctx, scope)).status, 400);
  assert.deepEqual(f.cancelled, []);
  assert.deepEqual(f.killed, []);
});
test("the private control channel transports scope and rejects scope on other endpoints", async () => {
  const root = {};
  const scope = { root: "root", sessions: [], jobs: [] };
  const release = registerControl(root, async (method, path, received) => ({
    method,
    path,
    received,
  }));
  try {
    assert.deepEqual(
      (await dispatchControl(root, { method: "POST", path: "/cancel", scope }))
        .received,
      { scope, admission: undefined },
    );
    assert.deepEqual(
      (
        await dispatchControl(root, {
          method: "POST",
          path: "/admission",
          admission: { task_id: "11111111-1111-4111-8111-111111111111" },
        })
      ).received,
      {
        scope: undefined,
        admission: { task_id: "11111111-1111-4111-8111-111111111111" },
      },
    );
    await assert.rejects(
      dispatchControl(root, { method: "GET", path: "/status", scope }),
    );
  } finally {
    release();
  }
});
