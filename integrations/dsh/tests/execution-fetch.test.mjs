import test from "node:test";
import assert from "node:assert/strict";
import { apply, createAttributedFetchProvider } from "../execution-fetch.mjs";
import { WEB_SESSION_CAP, claimWebCall } from "../execution-web-budget.mjs";

const RESULT = {
  url: "https://example.org/fixture",
  statusCode: 200,
  body: { kind: "text", content: "synthetic fixture" },
  truncated: false,
};

function fakeInner(outcome) {
  const calls = [];
  return {
    calls,
    id: "http",
    available: () => true,
    async fetch(request) {
      calls.push(request);
      if (outcome instanceof Error) throw outcome;
      return outcome;
    },
  };
}

function fakeAgents(sessionId, events) {
  const agent =
    sessionId === undefined
      ? undefined
      : {
          session: {
            id: sessionId,
            append: (name, payload) => events.push([name, payload]),
          },
        };
  return { currentInitiator: () => agent };
}

test("apply registers the attributed provider under the upstream http id", () => {
  let provider;
  apply({
    web: {
      registerFetchProvider: (value) => {
        provider = value;
      },
    },
    agents: { currentInitiator: () => undefined },
  });
  assert.equal(provider.id, "http");
  assert.equal(provider.available(), true);
});

test("fetch delegates and records request/result events for the initiating session", async () => {
  const events = [];
  const inner = fakeInner(RESULT);
  const provider = createAttributedFetchProvider(
    inner,
    fakeAgents("fetch-events-session", events),
  );
  const value = await provider.fetch({ url: "https://example.org/fixture" });
  assert.equal(value, RESULT);
  assert.equal(inner.calls.length, 1);
  assert.deepEqual(events, [
    ["web/fetch-request", { url: "https://example.org/fixture" }],
    [
      "web/fetch-result",
      { url: "https://example.org/fixture", statusCode: 200 },
    ],
  ]);
});

test("fetch records the error and rethrows when the inner provider fails", async () => {
  const events = [];
  const failure = new Error("synthetic fetch failure");
  const provider = createAttributedFetchProvider(
    fakeInner(failure),
    fakeAgents("fetch-error-session", events),
  );
  await assert.rejects(
    provider.fetch({ url: "https://example.org/broken" }),
    (error) => error === failure,
  );
  assert.deepEqual(events, [
    ["web/fetch-request", { url: "https://example.org/broken" }],
    [
      "web/fetch-result",
      { url: "https://example.org/broken", error: "synthetic fetch failure" },
    ],
  ]);
});

test("fetch without an initiator delegates without recording events", async () => {
  const inner = fakeInner(RESULT);
  const provider = createAttributedFetchProvider(
    inner,
    fakeAgents(undefined, []),
  );
  const value = await provider.fetch({ url: "https://example.org/fixture" });
  assert.equal(value, RESULT);
  assert.equal(inner.calls.length, 1);
});

test("the call past the session web cap is refused before delegating", async () => {
  const events = [];
  const inner = fakeInner(RESULT);
  const provider = createAttributedFetchProvider(
    inner,
    fakeAgents("fetch-cap-session", events),
  );
  for (let i = 0; i < WEB_SESSION_CAP; i++)
    await provider.fetch({ url: "https://example.org/fixture" });
  await assert.rejects(
    provider.fetch({ url: "https://example.org/fixture" }),
    (error) => error.code === "WEB_SESSION_BUDGET_EXCEEDED",
  );
  assert.equal(inner.calls.length, WEB_SESSION_CAP);
  assert.deepEqual(events.at(-1), [
    "web/budget-exceeded",
    { kind: "fetch", cap: WEB_SESSION_CAP },
  ]);
});

test("web budget counts per session and always allows unattributed calls", () => {
  assert.equal(WEB_SESSION_CAP, 30);
  for (let i = 0; i < WEB_SESSION_CAP + 5; i++)
    assert.equal(claimWebCall(undefined), true);
  for (let i = 0; i < WEB_SESSION_CAP; i++)
    assert.equal(claimWebCall("budget-session-a"), true);
  assert.equal(claimWebCall("budget-session-a"), false);
  assert.equal(claimWebCall("budget-session-b"), true);
});
