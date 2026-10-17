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
let lease = null,
  subscription = null,
  watchTimer = null,
  inspection = false,
  quietSince = 0;
const runningAgents = new Set();
let lastJobs = "";
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
  if (request.method === "GET" && request.url === "/model/budget") {
    const budget = active || lease;
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
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
    requestTimeoutMs: 180000,
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
    emit("jobs.cancelled");
  } else return stopping;
}
async function inspect() {
  if (!harness || closing || inspection) return;
  inspection = true;
  try {
    const status = JSON.parse(await control("GET", "/status"));
    const signature = JSON.stringify(status.jobs);
    if (signature !== lastJobs) {
      lastJobs = signature;
      emit("jobs.snapshot", { jobs: status.jobs });
    }
    const working = status.jobs.some((job) =>
      ["running", "stopping"].includes(job.status),
    );
    if (lease && !active && !working && !runningAgents.size) {
      if (!quietSince) quietSince = Date.now();
      else if (Date.now() - quietSince > 500) {
        clearTimeout(lease.timer);
        lease = null;
        emit("workspace.quiescent");
      }
    } else quietSince = 0;
  } finally {
    inspection = false;
  }
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
  active = {
    id: payload.id,
    cancelled: false,
    maxCalls: payload.max_calls,
    deadline: Date.now() + 180000,
  };
  if (lease) clearTimeout(lease.timer);
  lease = active;
  quietSince = 0;
  calls = 0;
  const owned = active;
  let outcome;
  emit("turn.started", { turn_id: owned.id });
  const timer = setTimeout(() => {
    emit("turn.deadline", { turn_id: owned.id });
    cancel().catch(() => {});
  }, 180000);
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
    if (outcome.type === "turn.failed") await cancel().catch(() => {});
    await stopping?.catch(() => {});
    stopping = null;
    active = null;
    if (owned.cancelled || outcome.type === "turn.failed") {
      clearTimeout(timer);
      if (lease === owned) lease = null;
    }
    await inspect().catch(() => {});
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
