import { test } from "node:test";
import assert from "node:assert/strict";
import { Readable } from "node:stream";
import { EventEmitter } from "node:events";
import { ClarificationBroker } from "../execution-clarifications.mjs";
import { apply } from "../execution-clarification-tool.mjs";

function fixture() {
  const events = [],
    sources = [];
  let owner = { id: "turn", deadline: Date.now() + 50000 };
  const broker = new ClarificationBroker({
    owner: () => owner,
    emit: (type, data) => events.push({ type, ...data }),
    addSource: (source) => sources.push(source),
  });
  const ask = async (extra = {}) => {
    const response = Object.assign(new EventEmitter(), {
      writeHead(status) {
        this.status = status;
      },
      end(body) {
        this.body = JSON.parse(body);
        this.writableEnded = true;
      },
    });
    await broker.request(
      Readable.from([
        Buffer.from(
          JSON.stringify({
            question: "按哪种规则执行？",
            options: ["保留原行为", "采用新行为"],
            session_id: "root",
            ...extra,
          }),
        ),
      ]),
      response,
    );
    return response;
  };
  return { broker, events, sources, ask, setOwner: (value) => (owner = value) };
}

test("pinned SDK compiles a nonparallel question tool", () => {
  const tools = [];
  apply({ tools: { register: (value) => tools.push(value) } });
  assert.equal(tools[0].name, "request_clarification");
  assert.equal(tools[0].isConcurrencySafe({}), false);
});

test("question waits for host input and does not invent a default", async () => {
  const { broker, events, sources, ask } = fixture();
  const response = await ask();
  const value = events[0].clarification;
  assert.equal(response.writableEnded, undefined);
  assert.equal(value.answer, null);
  assert.equal(sources.length, 0);
  broker.answer({
    request_id: value.id,
    turn_id: value.turn_id,
    answer: "由用户另行指定",
  });
  assert.equal(response.body.result.answer, "由用户另行指定");
  assert.equal(response.body.result.source, "user");
  assert.equal(sources.length, 1);
  broker.answer({
    request_id: value.id,
    turn_id: value.turn_id,
    answer: "替换",
  });
  assert.equal(sources.length, 1);
});

test("wrong turns, expired questions and cancellation cannot supply an answer", async () => {
  const { broker, events, sources, ask, setOwner } = fixture();
  const response = await ask();
  const value = events[0].clarification;
  broker.answer({ request_id: value.id, turn_id: "old", answer: "bad" });
  assert.equal(response.writableEnded, undefined);
  setOwner({ id: "new", deadline: Date.now() + 1000 });
  broker.answer({
    request_id: value.id,
    turn_id: value.turn_id,
    answer: "bad",
  });
  assert.equal(sources.length, 0);
  broker.cancel(value.turn_id, "user_cancelled");
  assert.equal(response.status, 409);
  assert.equal(broker.pending.size, 0);
});

test("invalid or excessive questions fail without waiting", async () => {
  const { broker, ask } = fixture();
  const tooLong = await ask({ options: ["a".repeat(81), "b"] });
  assert.equal(tooLong.status, 422);
  assert.match(tooLong.body.error.message, /options\[0\].*81.*80/);
  for (const extra of [
    { options: ["one"] },
    { options: ["same", " same "] },
    { question: "" },
    { options: ["a\nb", "b"] },
  ]) {
    assert.equal((await ask(extra)).status, 422);
  }
  await ask();
  await ask();
  await ask();
  assert.equal((await ask()).status, 429);
  broker.cancel(null, "closed");
});
