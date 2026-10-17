// Host-side owner. The provider credential arrives over stdin and never enters
// the sandbox. A revocable loopback gateway shares one budget across descendants.
import { DeepSeekHarness } from "@deepseek-ai/dsh-sdk-client";
import { createServer, request as httpRequest } from "node:http";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { createInterface } from "node:readline";
import { mkdir } from "node:fs/promises";
import { openSync, closeSync, constants } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const lines = createInterface({ input: process.stdin });
let config,
  harness,
  gateway,
  active = null,
  closing = false;
const token = randomBytes(32).toString("hex");
let sequence = 0,
  calls = 0,
  lifetimeCalls = 0;
let stopping = null;
const upstreamRequests = new Set();
const integration = dirname(fileURLToPath(import.meta.url));
function redact(value) {
  let serialized = JSON.stringify(value);
  for (const secret of [config?.api_key, token])
    if (secret) serialized = serialized.replaceAll(secret, "[REDACTED]");
  return serialized;
}
function emit(type, payload = {}) {
  process.stdout.write(
    redact({ sequence: ++sequence, type, ...payload }) + "\n",
  );
}
function forwardNotification(turnId, notification) {
  if (notification.method === "session.event") {
    const event = notification.params?.event;
    if (
      !event ||
      ![
        "tool/call",
        "tool/result",
        "turn/start",
        "turn/end",
        "assistant/message",
      ].includes(event.type)
    )
      return;
    let projected = event;
    if (event.type === "assistant/message")
      projected = {
        ...event,
        data: {
          turn: event.data.turn,
          step: event.data.step,
          message: {
            role: "assistant",
            content: (event.data.message?.content || []).filter(
              (item) => item.type === "text",
            ),
          },
        },
      };
    emit("runtime.event", {
      turn_id: turnId,
      notification: {
        method: notification.method,
        params: { sessionId: notification.params.sessionId, event: projected },
      },
    });
  } else if (
    ["subagent.started", "session.status"].includes(notification.method)
  )
    emit("runtime.event", { turn_id: turnId, notification });
  else if (notification.method === "subagent.finished") {
    const { lastAssistantMessage, ...params } = notification.params;
    emit("runtime.event", {
      turn_id: turnId,
      notification: { method: notification.method, params },
    });
  }
}
function errorResponse(response, code) {
  response.writeHead(code, { "Content-Type": "application/json" });
  response.end(
    JSON.stringify({
      type: "error",
      error: { type: "api_error", message: "Execution gateway request failed" },
    }),
  );
}
function control(method, path) {
  return new Promise((resolve, reject) => {
    const directory = openSync(
      config.state,
      constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
    );
    let closed = false;
    const release = () => {
      if (!closed) {
        closeSync(directory);
        closed = true;
      }
    };
    const request = httpRequest(
      { socketPath: `/proc/self/fd/${directory}/control.sock`, method, path },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          if (body.length < 100000) body += chunk;
        });
        response.on("end", () => {
          release();
          response.statusCode === 200
            ? resolve(body)
            : reject(new Error("Runtime control failed"));
        });
      },
    );
    request.setTimeout(7000, () =>
      request.destroy(new Error("Runtime control timed out")),
    );
    request.on("error", (error) => {
      release();
      reject(error);
    });
    request.end();
  });
}
async function proxy(request, response) {
  const supplied =
    request.headers["x-api-key"] ||
    request.headers.authorization?.replace(/^Bearer /, "") ||
    "";
  if (
    Buffer.byteLength(supplied) !== token.length ||
    !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))
  )
    return errorResponse(response, 401);
  if (
    request.method !== "POST" ||
    !["/model/messages", "/model/v1/messages"].includes(request.url)
  )
    return errorResponse(response, 404);
  if (
    !active ||
    active.cancelled ||
    calls >= active.maxCalls ||
    lifetimeCalls >= 200
  )
    return errorResponse(response, 429);
  // Reserve before await: root and concurrent descendants share a hard bound.
  calls++;
  lifetimeCalls++;
  const turnId = active.id;
  const chunks = [];
  let length = 0;
  for await (const chunk of request) {
    length += chunk.length;
    if (length > 2_000_000) return errorResponse(response, 413);
    chunks.push(chunk);
  }
  const abort = new AbortController();
  upstreamRequests.add(abort);
  const timeout = setTimeout(() => abort.abort(), 60000);
  try {
    const suffix = request.url.slice("/model".length);
    const headers = {
      "Content-Type": "application/json",
      "x-api-key": config.api_key,
      Authorization: `Bearer ${config.api_key}`,
      "anthropic-version": request.headers["anthropic-version"] || "2023-06-01",
    };
    const upstream = await fetch(config.endpoint.replace(/\/$/, "") + suffix, {
      method: "POST",
      headers,
      body: Buffer.concat(chunks),
      redirect: "error",
      signal: abort.signal,
    });
    emit("model.request", {
      turn_id: turnId,
      index: calls,
      lifetime_calls: lifetimeCalls,
      status: upstream.status,
    });
    if (!upstream.ok) return errorResponse(response, upstream.status);
    response.writeHead(upstream.status, {
      "Content-Type":
        upstream.headers.get("content-type") || "application/json",
    });
    const decoder = new TextDecoder();
    let pending = "";
    const secrets = [config.api_key, token];
    const retain = Math.max(...secrets.map((value) => value.length)) - 1;
    for await (const chunk of upstream.body) {
      pending += decoder.decode(chunk, { stream: true });
      for (const secret of secrets)
        pending = pending.replaceAll(secret, "[REDACTED]");
      const emitLength = Math.max(0, pending.length - retain);
      if (!response.destroyed) response.write(pending.slice(0, emitLength));
      else abort.abort();
      pending = pending.slice(emitLength);
    }
    pending += decoder.decode();
    for (const secret of secrets)
      pending = pending.replaceAll(secret, "[REDACTED]");
    if (!response.destroyed) response.write(pending);
    response.end();
  } catch {
    if (!response.headersSent) errorResponse(response, 502);
    else response.destroy();
  } finally {
    clearTimeout(timeout);
    upstreamRequests.delete(abort);
  }
}
async function makeHarness() {
  return new DeepSeekHarness({
    dshBin: join(integration, "isolated-entry.mjs"),
    profile: "sdk",
    patches: ["/opt/medical-agent/integrations/dsh/execution.patch.yml"],
    dshHome: "/state/home",
    processCwd: config.workspace,
    cwd: "/workspace",
    provider: "deepseek-official",
    model: config.model,
    reasoningEffort: "off",
    maxTokens: 4096,
    initializeTimeoutMs: 20000,
    requestTimeoutMs: 180000,
    disposeEofGraceMs: 1500,
    disposeGraceMs: 2000,
    env: {
      PATH: process.env.PATH || "/usr/bin:/bin",
      CHENGXIN_WORKSPACE: config.workspace,
      CHENGXIN_STATE: config.state,
      CHENGXIN_MODEL: config.model,
      DEEPSEEK_API_KEY: token,
      DEEPSEEK_BASE_URL: `http://127.0.0.1:${gateway.address().port}/model`,
    },
  });
}
async function setup(payload) {
  if (config) throw new Error("Already configured");
  if (
    ![
      payload.workspace,
      payload.state,
      payload.endpoint,
      payload.api_key,
      payload.model,
    ].every((v) => typeof v === "string" && v.length)
  )
    throw new Error("Missing configuration");
  const endpoint = new URL(payload.endpoint);
  if (
    !["https:", "http:"].includes(endpoint.protocol) ||
    endpoint.username ||
    endpoint.password ||
    endpoint.search ||
    endpoint.hash
  )
    throw new Error("Invalid provider endpoint");
  config = payload;
  await mkdir(join(config.state, "home"), { recursive: true });
  gateway = createServer((req, res) =>
    proxy(req, res).catch(() => errorResponse(res, 500)),
  );
  gateway.requestTimeout = 65000;
  await new Promise((resolve) => gateway.listen(0, "127.0.0.1", resolve));
  harness = await makeHarness();
  await harness.start();
  await control("GET", "/health");
  emit("ready", {
    runtime: "dsh-sdk",
    version: "0.2.0-rc.2",
    isolation: "bubblewrap-filesystem",
    network: "enabled",
  });
}
async function cancel() {
  if (stopping) return stopping;
  if (!harness) return;
  const ownedTurn = active;
  if (ownedTurn) ownedTurn.cancelled = true;
  for (const abort of upstreamRequests) abort.abort();
  stopping = control("POST", "/cancel").catch(async () => {
    const owned = harness;
    harness = null;
    await owned?.close();
    emit("runtime.control_failed", {
      turn_id: active?.id,
      reason:
        "Runtime closed to guarantee cancellation; create a new workspace to continue.",
    });
  });
  if (!ownedTurn) {
    await stopping;
    stopping = null;
    emit("jobs.cancelled");
  } else return stopping;
}
async function inspect() {
  if (!harness || closing) return;
  const status = JSON.parse(await control("GET", "/status"));
  emit("jobs.snapshot", { jobs: status.jobs });
}
async function run(payload) {
  if (!config || active || closing) {
    emit("rejected", { turn_id: payload.id, reason: "Runtime is not idle" });
    return;
  }
  if (
    typeof payload.text !== "string" ||
    !payload.text.trim() ||
    payload.text.length > 16000 ||
    !Number.isInteger(payload.max_calls) ||
    payload.max_calls < 1 ||
    payload.max_calls > 64
  ) {
    emit("rejected", { turn_id: payload.id, reason: "Invalid run" });
    return;
  }
  active = { id: payload.id, cancelled: false, maxCalls: payload.max_calls };
  calls = 0;
  const owned = active;
  let outcome;
  emit("turn.started", { turn_id: owned.id });
  const timer = setTimeout(() => {
    emit("turn.deadline", { turn_id: owned.id });
    cancel().catch(() => {});
  }, 180000);
  try {
    if (!harness) {
      harness = await makeHarness();
      await harness.start();
    }
    const result = await harness.run(payload.text, {
      sessionId: config.id,
      onNotification: (notification) =>
        forwardNotification(owned.id, notification),
    });
    const reason = result.events
      .filter((event) => event.type === "turn/end")
      .at(-1)?.data?.reason;
    const succeeded =
      reason?.kind === "completed" && Boolean(result.finalResponse.trim());
    outcome = {
      type: owned.cancelled
        ? "turn.cancelled"
        : succeeded
          ? "turn.completed"
          : "turn.failed",
      final_response: owned.cancelled ? "" : result.finalResponse,
      error_type:
        owned.cancelled || succeeded
          ? undefined
          : calls >= owned.maxCalls
            ? "model_call_budget_exhausted"
            : reason?.kind || "empty_response",
    };
  } catch (error) {
    outcome = {
      type: owned.cancelled ? "turn.cancelled" : "turn.failed",
      error_type: error.constructor.name,
      diagnostic: String(error.message).slice(0, 2000),
    };
  } finally {
    clearTimeout(timer);
    await stopping?.catch(() => {});
    stopping = null;
    active = null;
    const { type, ...fields } = outcome;
    emit(type, {
      turn_id: owned.id,
      model_calls: calls,
      lifetime_calls: lifetimeCalls,
      ...fields,
    });
  }
}
async function close() {
  if (closing) return;
  closing = true;
  await cancel();
  await harness?.close();
  for (const abort of upstreamRequests) abort.abort();
  gateway?.closeAllConnections();
  await new Promise((resolve) =>
    gateway ? gateway.close(resolve) : resolve(),
  );
  lines.close();
}
lines.on("line", (line) => {
  let payload;
  try {
    payload = JSON.parse(line);
  } catch {
    emit("protocol.error");
    return;
  }
  const operation =
    payload.method === "configure"
      ? setup(payload)
      : payload.method === "run"
        ? run(payload)
        : payload.method === "cancel"
          ? cancel()
          : payload.method === "inspect"
            ? inspect()
            : payload.method === "close"
              ? close()
              : Promise.reject(new Error("Unknown operation"));
  operation.catch((error) => {
    emit("worker.error", {
      error_type: error.constructor.name,
      diagnostic: String(error.message).slice(0, 4000),
    });
    if (!config || payload.method === "configure") close().catch(() => {});
  });
});
lines.on("close", () => {
  if (!closing) close().catch(() => {});
});
for (const signal of ["SIGTERM", "SIGINT"])
  process.on(signal, () => close().catch(() => {}));
