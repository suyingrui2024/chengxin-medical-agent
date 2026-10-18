import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const providerKey = "gateway-fixture-provider-key";
async function exercise(chunks, expected) {
  let requests = 0,
    credentialMatched = false;
  const server = createServer(async (request, response) => {
    for await (const _ of request) {
      /* drain the real request body */
    }
    requests++;
    credentialMatched = request.headers["x-api-key"] === providerKey;
    response.writeHead(200, { "content-type": "text/event-stream" });
    for (let index = 0; index < chunks.length; index++) {
      response.write(chunks[index]);
      if (index < chunks.length - 1)
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    response.end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const scratch = await mkdtemp(join(tmpdir(), "chengxin-gateway-stream-"));
  const child = spawn(
    process.execPath,
    [
      "--no-warnings",
      "--loader",
      new URL("../../../tests/fixtures/gateway-loader.mjs", import.meta.url)
        .href,
      fileURLToPath(new URL("../execution-worker.mjs", import.meta.url)),
    ],
    {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, NODE_OPTIONS: "" },
    },
  );
  const exited = new Promise((resolve) => child.once("exit", resolve));
  const lines = createInterface({ input: child.stdout });
  const events = [],
    waiters = new Set();
  let diagnostic = "";
  child.stderr.on("data", (bytes) => {
    diagnostic += bytes.toString();
  });
  lines.on("line", (line) => {
    const event = JSON.parse(line);
    events.push(event);
    for (const waiter of waiters) waiter(event);
  });
  const wait = (type) =>
    new Promise((resolve, reject) => {
      const existing = events.find((event) => event.type === type);
      if (existing) {
        resolve(existing);
        return;
      }
      const timer = setTimeout(() => {
        waiters.delete(check);
        reject(new Error(`Missing ${type}: ${diagnostic}`));
      }, 8000);
      const check = (event) => {
        if (event.type !== type && event.type !== "worker.error") return;
        clearTimeout(timer);
        waiters.delete(check);
        if (event.type === type) resolve(event);
        else reject(new Error(JSON.stringify(event)));
      };
      waiters.add(check);
    });
  const send = (payload) => child.stdin.write(JSON.stringify(payload) + "\n");
  try {
    send({
      method: "configure",
      id: randomUUID(),
      workspace: scratch,
      state: join(scratch, "state"),
      endpoint: `http://127.0.0.1:${server.address().port}/anthropic`,
      api_key: providerKey,
      model: "fixture",
      python: process.execPath,
    });
    await wait("ready");
    send({
      method: "run",
      id: randomUUID(),
      text: "Return the actual loopback stream.",
      max_calls: 1,
      time_budget_seconds: 10,
    });
    const result = await wait("turn.completed");
    assert.equal(result.model_calls, 1);
    assert.equal(result.final_response, expected);
    assert.equal(requests, 1);
    assert.equal(credentialMatched, true);
    assert.ok(!result.final_response.includes(providerKey));
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      send({ method: "close" });
      child.stdin.end();
    }
    const timer = setTimeout(() => child.kill("SIGKILL"), 2000);
    await exited;
    clearTimeout(timer);
    lines.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(scratch, { recursive: true, force: true });
  }
}

const prefix = 'data: {"text":"';
const emoji = prefix + "🙂" + "a".repeat(62);
const rareName = Buffer.from(prefix + "𠮷" + "a".repeat(62));
const beforeCharacter = Buffer.byteLength(prefix);
for (const [name, chunks] of [
  ["astral character at the redaction flush boundary", [emoji, '"}\n\n']],
  [
    "four-byte UTF-8 character split across upstream chunks",
    [
      rareName.subarray(0, beforeCharacter + 1),
      rareName.subarray(beforeCharacter + 1),
      Buffer.from('"}\n\n'),
    ],
  ],
  [
    "credential split across chunks alongside Unicode",
    [
      prefix + "中文🙂" + providerKey.slice(0, 9),
      providerKey.slice(9) + "𠮷" + "a".repeat(100),
      providerKey + '"}\n\n',
    ],
  ],
]) {
  test(
    `actual host gateway preserves ${name} (SDK double, no isolation)`,
    { timeout: 15000 },
    async () => {
      const expected = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)))
        .toString()
        .replaceAll(providerKey, "[REDACTED]");
      await exercise(chunks, expected);
    },
  );
}
