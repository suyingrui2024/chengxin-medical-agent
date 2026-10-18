// Native Windows regression: a delayed background child must retain the
// original task's model budget when a separate foreground prompt arrives.
// It uses the pinned official SDK, the real worker and ProcessContainer; the
// model is a loopback fixture and no provider credential is used.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

if (process.platform !== "win32")
  throw new Error("This regression requires native Windows ProcessContainer");

const scratch = await mkdtemp(join(tmpdir(), "chengxin-root-wakeup-"));
let rootCalls = 0;
let childCalls = 0;
let releaseChild;
const heldChild = new Promise((resolve) => {
  releaseChild = resolve;
});
const server = createServer(async (request, response) => {
  let raw = "";
  for await (const chunk of request) raw += chunk;
  const body = JSON.parse(raw);
  const root = JSON.stringify(body.messages || []).includes("ROOT_BUDGET_OWNER");
  const step = root ? ++rootCalls : ++childCalls;
  let block;
  if (root && step === 1)
    block = {
      type: "tool_use",
      id: "spawn-child",
      name: "subagent",
      input: {
        description: "Delayed synthetic child",
        prompt: "CHILD_BUDGET_OWNER: finish only after the fixture releases you.",
        run_in_background: true,
      },
    };
  else if (!root && step === 1) {
    await heldChild;
    block = { type: "text", text: "Child completed." };
  } else block = { type: "text", text: root ? "Root acknowledgement." : "Child completed." };
  const events = [
    { type: "message_start", message: { id: `${root ? "root" : "child"}${step}`, type: "message", role: "assistant", model: "fixture", content: [], usage: { input_tokens: 10, output_tokens: 1 } } },
    { type: "content_block_start", index: 0, content_block: block },
    { type: "content_block_stop", index: 0 },
    { type: "message_delta", delta: { stop_reason: block.type === "tool_use" ? "tool_use" : "end_turn" }, usage: { output_tokens: 10 } },
    { type: "message_stop" },
  ];
  response.writeHead(200, { "content-type": "text/event-stream" });
  response.end(events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(""));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

const root = process.env.CHENGXIN_EXECUTION_ROOT || fileURLToPath(new URL("../../..", import.meta.url));
const child = spawn(process.execPath, [join(root, "integrations/dsh/execution-worker.mjs")], {
  stdio: ["pipe", "pipe", "pipe"],
  env: { ...process.env, NODE_OPTIONS: "" },
});
const exited = new Promise((resolve) => child.once("exit", resolve));
const lines = createInterface({ input: child.stdout });
const events = [];
const waiters = new Set();
let diagnostic = "";
child.stderr.on("data", (data) => {
  diagnostic += data.toString();
});
lines.on("line", (line) => {
  const event = JSON.parse(line);
  events.push(event);
  for (const waiter of waiters) waiter(event);
});
const waitFor = (match, timeout = 30000) =>
  new Promise((resolve, reject) => {
    const present = events.find(match);
    if (present) return resolve(present);
    const timer = setTimeout(() => {
      waiters.delete(check);
      reject(new Error(`Missing expected event: ${diagnostic.slice(-1200)}`));
    }, timeout);
    const check = (event) => {
      if (!match(event) && event.type !== "worker.error") return;
      clearTimeout(timer);
      waiters.delete(check);
      if (match(event)) resolve(event);
      else reject(new Error(JSON.stringify(event)));
    };
    waiters.add(check);
  });
const send = (payload) => child.stdin.write(JSON.stringify(payload) + "\n");

try {
  const original = randomUUID();
  const followup = randomUUID();
  send({
    method: "configure",
    id: randomUUID(),
    workspace: scratch,
    state: join(scratch, "state"),
    endpoint: `http://127.0.0.1:${server.address().port}/anthropic`,
    api_key: "windows-root-wakeup-loopback-only",
    model: "fixture",
    python: process.env.MEDAGENT_PYTHON,
  });
  const ready = await waitFor((event) => event.type === "ready");
  assert.equal(ready.isolation, "windows-processcontainer");
  send({
    method: "run",
    id: original,
    text: "ROOT_BUDGET_OWNER: start the synthetic child and acknowledge.",
    max_calls: 3,
    time_budget_seconds: 30,
  });
  await waitFor((event) => event.type === "turn.completed" && event.turn_id === original);
  const childDeadline = Date.now() + 10000;
  while (childCalls !== 1 && Date.now() < childDeadline)
    await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(childCalls, 1, "the delayed child must have reached the fixture");
  send({
    method: "run",
    id: followup,
    text: "ROOT_BUDGET_OWNER: acknowledge an independent follow-up.",
    max_calls: 1,
    time_budget_seconds: 30,
  });
  await waitFor((event) => event.type === "turn.completed" && event.turn_id === followup);
  releaseChild();
  await waitFor(
    (event) =>
      event.type === "runtime.event" &&
      event.notification?.method === "subagent.finished",
  );
  await new Promise((resolve) => setTimeout(resolve, 300));
  const requests = events.filter((event) => event.type === "model.request");
  assert.equal(requests.filter((event) => event.turn_id === original).length, 3);
  assert.equal(requests.filter((event) => event.turn_id === followup).length, 1);
  assert.equal(requests.length, 4);
  console.log(JSON.stringify({
    status: "passed",
    isolation: ready.isolation,
    original_task_calls: 3,
    followup_task_calls: 1,
    old_completion_calls_charged_to_followup: 0,
  }));
} finally {
  releaseChild?.();
  if (child.exitCode === null && child.signalCode === null) {
    send({ method: "close" });
    child.stdin.end();
  }
  const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
  await exited;
  clearTimeout(timer);
  lines.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
  await rm(scratch, { recursive: true, force: true });
}
