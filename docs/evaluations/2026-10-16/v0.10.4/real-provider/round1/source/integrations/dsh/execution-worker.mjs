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
import { execFile } from "node:child_process";
import { hasActiveJobs, observationKey } from "./execution-observations.mjs";
import { ClarificationBroker } from "./execution-clarifications.mjs";

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
let lease = null,
  subscription = null,
  watchTimer = null,
  inspection = null,
  quietSince = 0;
const runningAgents = new Set();
let lastJobs = "";
let observationEpoch = "",
  observationSequence = 0;
const callOwners = new Map();
const pendingObservations = new Map();
const upstreamRequests = new Set();
let capabilityLifetimeCalls = 0;
let workspaceLifetimeCalls = 0;
const integration = dirname(fileURLToPath(import.meta.url));
const clarifications = new ClarificationBroker({
  owner: () => active || lease,
  emit: (type, value) => emit(type, value),
  addSource: (source) => {
    config.user_turns = [...config.user_turns, source].slice(-64);
  },
});
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
    if (event.type === "tool/call" && turnId) {
      const data = event.data;
      callOwners.set(
        observationKey(
          notification.params.sessionId,
          data.turn,
          data.step,
          data.callId,
        ),
        turnId,
      );
      if (callOwners.size > 4096)
        callOwners.delete(callOwners.keys().next().value);
    }
    if (event.type === "assistant/message")
      projected = {
        ...event,
        data: {
          turn: event.data.turn,
          step: event.data.step,
          usage: event.data.usage,
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
    if (event.type === "tool/call" && turnId) {
      const data = event.data;
      const key = observationKey(
        notification.params.sessionId,
        data.turn,
        data.step,
        data.callId,
      );
      const observation = pendingObservations.get(key);
      if (observation) {
        emit("tool.observations", {
          observations: [{ ...observation, turn_id: turnId }],
        });
        pendingObservations.delete(key);
        callOwners.delete(key);
      }
    }
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
function observeRuntime() {
  subscription?.close();
  runningAgents.clear();
  subscription = harness.client.subscribeSessionTree(config.id);
  const owned = subscription;
  (async () => {
    try {
      for await (const notification of owned) {
        if (
          notification.method === "session.event" &&
          notification.params.sessionId === config.id &&
          notification.params.event?.type === "turn/start"
        )
          config.require_resume = true;
        if (notification.method === "session.status") {
          if (notification.params.status === "idle")
            runningAgents.delete(notification.params.sessionId);
          else runningAgents.add(notification.params.sessionId);
        }
        if (notification.method === "subagent.finished")
          runningAgents.delete(notification.params.childSessionId);
        const owner = active || lease;
        forwardNotification(owner?.id || config.id, notification);
        if (
          !active &&
          lease &&
          notification.method === "session.event" &&
          notification.params.sessionId === config.id &&
          notification.params.event?.type === "assistant/message"
        ) {
          const message = notification.params.event.data.message;
          const content = (message.content || [])
            .filter((item) => item.type === "text")
            .map((item) => item.text)
            .join("\n");
          if (content)
            emit("turn.background_reply", { turn_id: lease.id, text: content });
        }
      }
    } catch {
      /* SDK teardown closes the owned subscription. */
    }
  })();
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
          body += chunk;
          if (body.length > 512000)
            request.destroy(new Error("Runtime control response too large"));
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
async function capabilityRequest(request, response) {
  const owned = active || lease;
  const fail = (status, code, message) => {
    if (!response.headersSent && !response.destroyed) {
      response.writeHead(status, { "Content-Type": "application/json" });
      response.end(redact({ ok: false, error: { code, message } }));
    }
  };
  if (!owned || owned.cancelled || Date.now() >= owned.deadline)
    return fail(409, "capability_turn_inactive", "当前没有可执行的任务。");
  if (owned.capabilityCalls >= 32 || capabilityLifetimeCalls >= 256)
    return fail(429, "capability_budget_exhausted", "已达到注册能力调用上限。");
  owned.capabilityCalls++;
  capabilityLifetimeCalls++;
  const operation = request.method === "GET" ? "catalog" : "execute";
  let args = {};
  try {
    let size = 0;
    const chunks = [];
    for await (const chunk of request) {
      size += chunk.length;
      if (size > 16000) return fail(413, "request_too_large", "计算请求过大。");
      chunks.push(chunk);
    }
    if (operation === "execute") args = JSON.parse(Buffer.concat(chunks));
    if (!args || typeof args !== "object" || Array.isArray(args))
      return fail(400, "invalid_arguments", "计算请求必须是 JSON 对象。");
  } catch {
    return fail(400, "invalid_arguments", "请提交结构化计算参数。");
  }
  const sources = [];
  // Only exact-quote candidates are passed to the trusted, credential-free helper.
  for (const source of [...config.user_turns].reverse()) {
    if (
      typeof args.evidence === "string" &&
      source.text.includes(args.evidence)
    ) {
      if (Buffer.byteLength(JSON.stringify([...sources, source])) > 100000)
        break;
      sources.push(source);
    }
  }
  sources.reverse();
  const abort = new AbortController();
  upstreamRequests.add(abort);
  const timeout = setTimeout(
    () => abort.abort(),
    Math.min(5000, Math.max(1, owned.deadline - Date.now())),
  );
  try {
    const payload = {
      operation,
      binding: config.capability_binding ?? null,
      sources,
      request: args,
      workspace_id: config.id,
      turn_id: owned.id,
    };
    const reply = await new Promise((resolve, reject) => {
      const child = execFile(
        config.python,
        [
          "-I",
          "-c",
          "import sys;sys.path.insert(0,sys.argv[1]);from medagent.execution_capabilities import main;main()",
          join(integration, "../../src"),
        ],
        {
          cwd: integration,
          env: { PATH: process.env.PATH || "/usr/bin:/bin", LANG: "C.UTF-8" },
          signal: abort.signal,
          timeout: 5000,
          maxBuffer: 128000,
        },
        (error, stdout) => {
          if (error) return reject(error);
          try {
            resolve(JSON.parse(stdout));
          } catch (invalid) {
            reject(invalid);
          }
        },
      );
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(payload));
    });
    if (
      owned.cancelled ||
      (active || lease) !== owned ||
      Date.now() >= owned.deadline
    )
      return fail(
        409,
        "capability_turn_inactive",
        "计算回合已停止，未提交成功回执。",
      );
    if (operation === "execute") {
      emit(reply.ok ? "capability.completed" : "capability.failed", {
        turn_id: owned.id,
        ...(reply.ok ? { result: reply.result } : { error: reply.error }),
      });
    }
    response.writeHead(reply.ok ? 200 : 422, {
      "Content-Type": "application/json",
    });
    response.end(redact(reply));
  } catch {
    if (operation === "execute")
      emit("capability.failed", {
        turn_id: owned.id,
        error: {
          code: "capability_dispatch_failed",
          message: "受信计算未完成。",
        },
      });
    fail(
      503,
      "capability_dispatch_failed",
      "受信计算进程未完成，未生成成功回执。",
    );
  } finally {
    clearTimeout(timeout);
    upstreamRequests.delete(abort);
  }
}
async function workspaceRequest(request, response) {
  const owned = active || lease;
  const reply = (status, value) => {
    if (response.destroyed || response.writableEnded) return;
    response.writeHead(status, { "Content-Type": "application/json" });
    response.end(redact(value));
  };
  const fail = (status, message) =>
    reply(status, {
      ok: false,
      error: { code: "workspace_changes_unavailable", message },
    });
  if (!owned || owned.cancelled || Date.now() >= owned.deadline)
    return fail(409, "当前任务已经停止。");
  if (owned.workspaceCalls >= 32 || workspaceLifetimeCalls >= 256)
    return fail(429, "已达到文件核对调用上限。");
  owned.workspaceCalls++;
  workspaceLifetimeCalls++;
  let args;
  try {
    const chunks = [];
    let bytes = 0;
    for await (const chunk of request) {
      bytes += chunk.length;
      if (bytes > 4000) return fail(413, "文件核对参数过大。");
      chunks.push(chunk);
    }
    args = JSON.parse(Buffer.concat(chunks));
    if (
      !args ||
      typeof args !== "object" ||
      Array.isArray(args) ||
      Object.keys(args).some(
        (key) => !["path", "offset", "limit"].includes(key),
      )
    )
      return fail(400, "文件核对参数无效。");
  } catch {
    return fail(400, "文件核对参数无效。");
  }
  const abort = new AbortController();
  upstreamRequests.add(abort);
  const timeout = setTimeout(
    () => abort.abort(),
    Math.min(10000, Math.max(1, owned.deadline - Date.now())),
  );
  try {
    const result = await new Promise((resolve, reject) => {
      const child = execFile(
        config.python,
        [
          "-I",
          "-c",
          "import sys;sys.path.insert(0,sys.argv[1]);from medagent.execution_changes import main;main()",
          join(integration, "../../src"),
        ],
        {
          cwd: integration,
          env: { PATH: process.env.PATH || "/usr/bin:/bin", LANG: "C.UTF-8" },
          signal: abort.signal,
          timeout: 10000,
          maxBuffer: 512000,
        },
        (error, stdout) => {
          if (error) return reject(error);
          try {
            resolve(JSON.parse(stdout));
          } catch (invalid) {
            reject(invalid);
          }
        },
      );
      child.stdin.on("error", () => {});
      child.stdin.end(
        JSON.stringify({
          directory: config.workspace,
          identity: config.directory_identity,
          store_root: config.store_root,
          workspace_id: config.id,
          turn_id: owned.id,
          secret: config.api_key,
          path: args.path,
          offset: args.offset,
          limit: args.limit,
        }),
      );
    });
    if (
      owned.cancelled ||
      (active || lease) !== owned ||
      Date.now() >= owned.deadline
    )
      return fail(409, "当前任务已经停止。");
    reply(result.ok ? 200 : 422, result);
  } catch {
    fail(503, "文件核对进程未能完成。");
  } finally {
    clearTimeout(timeout);
    upstreamRequests.delete(abort);
  }
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
    (request.method === "GET" && request.url === "/model/capabilities") ||
    (request.method === "POST" && request.url === "/model/capabilities/execute")
  )
    return capabilityRequest(request, response);
  if (request.method === "POST" && request.url === "/model/workspace/changes")
    return workspaceRequest(request, response);
  if (request.method === "POST" && request.url === "/model/clarifications")
    return clarifications.request(request, response);
  if (request.method === "GET" && request.url === "/model/budget") {
    const budget = active || lease;
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        turn_id: budget?.id || null,
        remaining_calls: budget
          ? Math.max(0, Math.min(budget.maxCalls - calls, 200 - lifetimeCalls))
          : 0,
        remaining_seconds: budget
          ? Math.max(0, Math.floor((budget.deadline - Date.now()) / 1000))
          : 0,
      }),
    );
    return;
  }
  if (
    request.method !== "POST" ||
    !["/model/messages", "/model/v1/messages"].includes(request.url)
  )
    return errorResponse(response, 404);
  const budget = active || lease;
  if (
    !budget ||
    budget.cancelled ||
    calls >= budget.maxCalls ||
    lifetimeCalls >= 200
  )
    return errorResponse(response, 429);
  // Reserve before await: root and concurrent descendants share a hard bound.
  calls++;
  lifetimeCalls++;
  const reservedIndex = calls;
  const reservedLifetimeIndex = lifetimeCalls;
  const turnId = budget.id;
  const chunks = [];
  let length = 0;
  for await (const chunk of request) {
    length += chunk.length;
    if (length > 2_000_000) return errorResponse(response, 413);
    chunks.push(chunk);
  }
  const body = Buffer.concat(chunks);
  let modelRequest;
  try {
    modelRequest = JSON.parse(body);
  } catch {
    return errorResponse(response, 400);
  }
  if (
    !Number.isInteger(modelRequest.max_tokens) ||
    modelRequest.max_tokens < 1 ||
    modelRequest.max_tokens > 8192
  )
    return errorResponse(response, 400);
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
      body,
      redirect: "error",
      signal: abort.signal,
    });
    emit("model.request", {
      turn_id: turnId,
      index: reservedIndex,
      lifetime_calls: reservedLifetimeIndex,
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
    maxTokens: 8192,
    initializeTimeoutMs: 20000,
    // The owner timer, shared by all descendants, enforces the selected budget.
    requestTimeoutMs: 1820000,
    disposeEofGraceMs: 1500,
    disposeGraceMs: 2000,
    env: {
      PATH: process.env.PATH || "/usr/bin:/bin",
      CHENGXIN_WORKSPACE: config.workspace,
      CHENGXIN_STATE: config.state,
      CHENGXIN_MODEL: config.model,
      CHENGXIN_RESUME_SESSION_ID: config.require_resume ? config.id : "",
      DEEPSEEK_API_KEY: token,
      DEEPSEEK_BASE_URL: `http://127.0.0.1:${gateway.address().port}/model`,
      ...Object.fromEntries(
        ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY"]
          .filter((key) => process.env[key])
          .map((key) => [key, process.env[key]]),
      ),
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
      payload.python,
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
  config.user_turns = Array.isArray(payload.user_turns)
    ? payload.user_turns.slice(-64)
    : [];
  await mkdir(join(config.state, "home"), { recursive: true });
  gateway = createServer((req, res) =>
    proxy(req, res).catch(() => errorResponse(res, 500)),
  );
  gateway.requestTimeout = 65000;
  await new Promise((resolve) => gateway.listen(0, "127.0.0.1", resolve));
  harness = await makeHarness();
  await harness.start();
  await control("GET", "/health");
  observeRuntime();
  watchTimer = setInterval(() => inspect().catch(() => {}), 700);
  emit("ready", {
    runtime: "dsh-sdk",
    version: "0.2.0-rc.2",
    isolation: "bubblewrap-filesystem",
    network: "enabled",
    model: config.model,
  });
}
async function cancel() {
  if (stopping) return stopping;
  if (!harness) return;
  const ownedTurn = active;
  const ownedBudget = active || lease;
  clarifications.cancel(
    ownedBudget?.id,
    ownedBudget?.timedOut ? "time_budget_exhausted" : "user_cancelled",
  );
  if (active || lease) (active || lease).cancelled = true;
  for (const abort of upstreamRequests) abort.abort();
  stopping = control("POST", "/cancel").catch(async () => {
    const owned = harness;
    harness = null;
    await owned?.close();
    emit("runtime.control_failed", {
      turn_id: active?.id,
      reason:
        "Runtime closed to guarantee cancellation; the next task will verify and resume persisted history.",
    });
  });
  if (!ownedTurn) {
    await stopping;
    stopping = null;
    if (lease) clearTimeout(lease.timer);
    lease = null;
    emit("jobs.cancelled", {
      turn_id: ownedBudget?.id,
      reason: ownedBudget?.timedOut
        ? "time_budget_exhausted"
        : "user_cancelled",
    });
  } else return stopping;
}
function inspect(fresh = false) {
  if (!harness || closing) return Promise.resolve();
  if (inspection) return inspection.then(() => (fresh ? inspect() : undefined));
  const owned = inspectNow().finally(() => {
    if (inspection === owned) inspection = null;
  });
  inspection = owned;
  return owned;
}
async function inspectNow() {
  let status;
  for (let page = 0; page < 8; page++) {
    status = JSON.parse(
      await control(
        "GET",
        `/status?after=${observationSequence}&epoch=${encodeURIComponent(observationEpoch)}`,
      ),
    );
    const batch = status.observations;
    if (!batch || !Array.isArray(batch.items) || !Array.isArray(status.jobs))
      throw new Error("Invalid runtime status");
    if (batch.epoch !== observationEpoch) {
      observationEpoch = batch.epoch;
      observationSequence = 0;
    }
    if (batch.first_sequence > observationSequence + 1) {
      emit("tool.observation_gap", {
        turn_id: (active || lease)?.id,
        missing: batch.first_sequence - observationSequence - 1,
      });
      observationSequence = batch.first_sequence - 1;
    }
    const values = [];
    for (const observation of batch.items) {
      if (
        !Number.isSafeInteger(observation.sequence) ||
        observation.sequence <= observationSequence
      )
        continue;
      const key = observationKey(
        observation.session_id,
        observation.sdk_turn,
        observation.sdk_step,
        observation.call_id,
      );
      const turnId = callOwners.get(key);
      observationSequence = observation.sequence;
      if (turnId) {
        values.push({ ...observation, turn_id: turnId });
        callOwners.delete(key);
      } else {
        pendingObservations.set(key, observation);
        if (pendingObservations.size > 256)
          pendingObservations.delete(pendingObservations.keys().next().value);
      }
    }
    if (values.length) emit("tool.observations", { observations: values });
    if (observationSequence >= batch.last_sequence || !batch.items.length)
      break;
  }
  const signature = JSON.stringify(status.jobs);
  if (signature !== lastJobs) {
    lastJobs = signature;
    emit("jobs.snapshot", { jobs: status.jobs });
  }
  const working = hasActiveJobs(status.jobs);
  if (
    lease &&
    !active &&
    !working &&
    !runningAgents.size &&
    !clarifications.pending.size
  ) {
    if (!quietSince) quietSince = Date.now();
    else if (Date.now() - quietSince > 500) {
      clearTimeout(lease.timer);
      lease = null;
      emit("workspace.quiescent");
    }
  } else quietSince = 0;
}
async function run(payload) {
  if (!config || active || closing) {
    emit("rejected", { turn_id: payload.id, reason: "Runtime is not idle" });
    return;
  }
  const seconds = Object.hasOwn(payload, "time_budget_seconds")
    ? payload.time_budget_seconds
    : 180;
  if (
    typeof payload.text !== "string" ||
    !payload.text.trim() ||
    payload.text.length > 16000 ||
    !Number.isInteger(payload.max_calls) ||
    payload.max_calls < 1 ||
    payload.max_calls > 64 ||
    !Number.isInteger(seconds) ||
    seconds < 1 ||
    seconds > 1800
  ) {
    emit("rejected", { turn_id: payload.id, reason: "Invalid run" });
    return;
  }
  active = {
    id: payload.id,
    cancelled: false,
    maxCalls: payload.max_calls,
    deadline: Date.now() + seconds * 1000,
    capabilityCalls: 0,
    workspaceCalls: 0,
  };
  if (lease) clearTimeout(lease.timer);
  lease = active;
  quietSince = 0;
  calls = 0;
  const owned = active;
  config.user_turns = [
    ...config.user_turns.filter((turn) => turn.id !== payload.id),
    { id: payload.id, text: payload.text },
  ].slice(-64);
  let outcome;
  emit("turn.started", { turn_id: owned.id, time_budget_seconds: seconds });
  const timer = setTimeout(() => {
    owned.timedOut = true;
    emit("turn.deadline", { turn_id: owned.id, time_budget_seconds: seconds });
    cancel().catch(() => {});
  }, seconds * 1000);
  owned.timer = timer;
  try {
    if (!harness) {
      harness = await makeHarness();
      await harness.start();
      observeRuntime();
    }
    const guidance = `\n\n[执行上下文] 本轮主任务、子智能体和辅助搜索合计最多 ${owned.maxCalls} 次模型请求；为实际运行、保存要求的产物和最终回复留出调用。批量读取相关文件，执行有针对性的检查；通过后及时收尾。简单任务无需额外委托。用户要求委托或任务需要分工时，提前安排范围明确的子任务；下一步依赖其结果时设置 run_in_background=false，避免父子重复检查与 sleep 轮询。`;
    let result = await harness.run(payload.text + guidance, {
      sessionId: config.id,
    });
    // One bounded recovery for a truncated output, within this same user task.
    // Do not reset calls, deadline, lease, SDK process or original session.
    const firstReason = result.events
      .filter((event) => event.type === "turn/end")
      .at(-1)?.data?.reason;
    if (
      firstReason?.kind === "max-tokens" &&
      !owned.cancelled &&
      owned.maxCalls - calls >= 2 &&
      200 - lifetimeCalls >= 2 &&
      owned.deadline - Date.now() > 15000
    ) {
      emit("turn.recovering", {
        turn_id: owned.id,
        reason: "max-tokens",
        attempt: 1,
        remaining_calls: Math.min(owned.maxCalls - calls, 200 - lifetimeCalls),
      });
      result = await harness.run(
        "[执行恢复] 上一次模型输出达到单次上限，原任务尚未完成。继续本轮最初的用户任务，已有文件和工具结果仍在当前会话。不要重做已完成的工作；按较小步骤调用工具、写入或修改必要文件并实际验证。不要长篇推演或增加任务外审计。共享调用预算与截止时间未重置；只允许这一次自动恢复，完成后简短报告真实结果。",
        { sessionId: config.id },
      );
    }
    // One repair of explicit acceptance checks, without renewing any budget.
    const failedChecks = async () => {
      const status = JSON.parse(await control("GET", "/status"));
      if (status.incomplete_delivery_turns?.includes(owned.id))
        return [
          {
            check_label: "检查记录不完整",
            state: "unavailable",
            exit_code: null,
          },
        ];
      return (status.delivery_checks || []).filter(
        (value) =>
          value.check_turn_id === owned.id &&
          value.check_required !== false &&
          (value.state !== "completed" || value.exit_code !== 0),
      );
    };
    let unresolved = await failedChecks();
    const beforeRepair = result.events
      .filter((event) => event.type === "turn/end")
      .at(-1)?.data?.reason;
    if (
      beforeRepair?.kind === "completed" &&
      result.finalResponse.trim() &&
      unresolved.length &&
      !owned.cancelled &&
      owned.maxCalls - calls >= 2 &&
      200 - lifetimeCalls >= 2 &&
      owned.deadline - Date.now() > 15000
    ) {
      emit("turn.check_repairing", {
        turn_id: owned.id,
        attempt: 1,
        remaining_calls: Math.min(owned.maxCalls - calls, 200 - lifetimeCalls),
        checks: unresolved.map(({ check_label, state, exit_code }) => ({
          label: check_label,
          state,
          exit_code,
        })),
      });
      result = await harness.run(
        "[交付检查纠错] 本轮已声明的检查尚未解决：" +
          JSON.stringify(
            unresolved.map(({ check_label, check_key, state, exit_code }) => ({
              label: check_label,
              check_key,
              state,
              exit_code,
            })),
          ) +
          "。检查命令与实际输出保留在当前会话。遵守原用户的文件保护、授权与任务范围；只要求检查报告时不得扩展为修改。先判断实现、测试预期或当前版本API是否有误，在授权范围内修正后用原delivery_check命令复验。不得用无关命令替代，也不能隐藏失败。若范围、预算或时间不足，请如实说明未完成。调用额度和截止时间未重置，只允许本次自动纠错。",
        { sessionId: config.id },
      );
      unresolved = await failedChecks();
    }
    const reason = result.events
      .filter((event) => event.type === "turn/end")
      .at(-1)?.data?.reason;
    const succeeded =
      reason?.kind === "completed" &&
      Boolean(result.finalResponse.trim()) &&
      !unresolved.length;
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
          : unresolved.length && reason?.kind === "completed"
            ? "delivery_checks_failed"
            : calls >= owned.maxCalls
              ? "model_call_budget_exhausted"
              : unresolved.length
                ? "delivery_checks_failed"
                : reason?.kind || "empty_response",
    };
  } catch (error) {
    outcome = {
      type: owned.cancelled ? "turn.cancelled" : "turn.failed",
      error_type: error.constructor.name,
      diagnostic: String(error.message).slice(0, 2000),
    };
  } finally {
    if (outcome.type === "turn.failed") await cancel().catch(() => {});
    await stopping?.catch(() => {});
    stopping = null;
    active = null;
    if (owned.cancelled || outcome.type === "turn.failed") {
      clearTimeout(timer);
      if (lease === owned) lease = null;
    }
    await inspect(true).catch(() => {});
    if (owned.timedOut) outcome.error_type = "time_budget_exhausted";
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
  clearInterval(watchTimer);
  if (lease) clearTimeout(lease.timer);
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
      : payload.method === "clarification.answer"
        ? Promise.resolve(clarifications.answer(payload))
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
