import test from "node:test";
import assert from "node:assert/strict";
import {
  ExecutionOwnership,
  scopedModelRequest,
} from "../execution-ownership.mjs";

function fixture() {
  const first = { id: "original", cancelled: false };
  const next = { id: "followup", cancelled: false };
  let current = first;
  const ledger = new ExecutionOwnership(
    () => current,
    () => "root",
  );
  ledger.remember(first);
  ledger.remember(next);
  return {
    ledger,
    first,
    next,
    advance: () => {
      current = next;
    },
  };
}

test("a child and grandchild retain their original grant across a foreground follow-up", async () => {
  const { ledger, first, next, advance } = fixture();
  ledger.bind("root", "child");
  advance();
  ledger.bind("child", "grandchild");
  assert.equal(
    await ledger.forRequest({ "x-deepseek-harness-session-id": "child" }),
    first,
  );
  assert.equal(
    await ledger.forRequest({ "x-chengxin-session-id": "grandchild" }),
    first,
  );
  assert.equal(
    await ledger.forRequest({ "x-chengxin-session-id": "root" }),
    next,
  );
});

test("canonical returned turns protect a delayed creation notification from a newer foreground grant", () => {
  const { ledger, first, next, advance } = fixture();
  ledger.recordTurn("root", 1, first);
  advance();
  assert.equal(ledger.eventOwner("root", { data: { turn: 1 } }), first);
  ledger.bind("root", "late-child");
  assert.equal(ledger.forSession("late-child"), first);
  ledger.recordTurn("root", 2, next);
  ledger.eventOwner("root", { data: { turn: 2 } });
  ledger.bind("root", "new-child");
  assert.equal(ledger.forSession("new-child"), next);
  assert.equal(ledger.forSdkTurn("root", 1), first);
  assert.equal(ledger.forSdkTurn("root", 2), next);
  assert.equal(ledger.forSdkTurn("root", 999), undefined);
  ledger.bind("root", "after-reading-an-old-job");
  assert.equal(ledger.forSession("after-reading-an-old-job"), next);
});

test("cancellation sessions follow descendant grants while the shared root follows the foreground", () => {
  const { ledger, first, next, advance } = fixture();
  ledger.bind("root", "child");
  advance();
  ledger.bind("child", "grandchild");
  assert.deepEqual(ledger.sessionsFor(first), ["child", "grandchild"]);
  assert.deepEqual(ledger.sessionsFor(next), ["root"]);
});

test("an HTTP request can wait for canonical creation without adopting the foreground grant", async () => {
  const { ledger, first } = fixture();
  const waiting = ledger.forRequest({
    "x-chengxin-session-id": "created-next",
  });
  ledger.bind("root", "created-next");
  assert.equal(await waiting, first);
  assert.equal(ledger.waiters.size, 0);
});

test("unknown or conflicting sessions never fall back to the latest foreground grant", async () => {
  const { ledger, advance } = fixture();
  advance();
  assert.equal(
    await ledger.forRequest({ "x-chengxin-session-id": "unknown" }),
    undefined,
  );
  assert.equal(
    await ledger.forRequest({
      "x-chengxin-session-id": "root",
      "x-deepseek-harness-session-id": "unknown",
    }),
    undefined,
  );
  assert.equal(ledger.waiters.size, 0);
});

test("workspace cancellation revokes all retained grants and evicted grants cannot be adopted", () => {
  const { ledger, first, next, advance } = fixture();
  ledger.bind("root", "old-child");
  advance();
  ledger.cancelAll();
  assert.equal(first.cancelled, true);
  assert.equal(next.cancelled, true);
  for (let n = 0; n < 256; n++) ledger.remember({ id: `later-${n}` });
  assert.equal(ledger.forSession("old-child"), undefined);
  assert.ok(ledger.turns.size <= 256);
});

test("scoped search routing preserves only the session and supported Messages path", () => {
  assert.deepEqual(scopedModelRequest("/model/sessions/child-id/messages"), {
    sessionId: "child-id",
    path: "/model/messages",
  });
  assert.deepEqual(scopedModelRequest("/model/sessions/child-id/v1/messages"), {
    sessionId: "child-id",
    path: "/model/v1/messages",
  });
  assert.deepEqual(scopedModelRequest("/model/sessions/a%2Fb/messages"), {
    invalid: true,
  });
  assert.deepEqual(scopedModelRequest("/model/sessions/%ZZ/messages"), {
    invalid: true,
  });
  assert.equal(
    scopedModelRequest("/model/sessions/child-id/runtime/realpath"),
    null,
  );
});
