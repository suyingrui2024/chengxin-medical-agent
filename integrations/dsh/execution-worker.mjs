// Host-side owner. The provider credential arrives over stdin and never enters
// the sandbox. A revocable loopback gateway shares one budget across descendants.
import { DeepSeekHarness } from "@deepseek-ai/dsh-sdk-client";
import { createServer } from "node:http";
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { createInterface } from "node:readline";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { execFile } from "node:child_process";
import { createPathResolver } from "../windows/path-broker.mjs";
import { hasActiveJobs, observationKey } from "./execution-observations.mjs";
import { ClarificationBroker } from "./execution-clarifications.mjs";
import { runOwnedPrompt } from "./execution-prompt.mjs";
import { checkArguments } from "./execution-delivery-contract.mjs";
import {
  ExecutionOwnership,
  scopedModelRequest,
} from "./execution-ownership.mjs";
import {
  checkpointPaths,
  mergeCheckpointPaths,
  coversCheckpointPaths,
  coversVerifiedInputs,
  validCheckpoint,
} from "./execution-checkpoints.mjs";

const lines = createInterface({ input: process.stdin });
let config,
  harness,
  gateway,
  active = null,
  closing = false;
let resolveNativePath,
  metadataCalls = 0;
const token = randomBytes(32).toString("hex");
// This grant can only query approved path/directory metadata. It cannot call a
// model or another host tool, and survives the SDK's child credential scrub.
const metadataAuth = randomBytes(32).toString("hex");
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
const upstreamRequests = new Map();
const jobOwners = new Map();
let capabilityLifetimeCalls = 0;
let workspaceLifetimeCalls = 0;
let checkpointLifetimeCalls = 0;
const integration = dirname(fileURLToPath(import.meta.url));
const ownership = new ExecutionOwnership(
  () => active || lease,
  () => config?.id,
);
function trustedHelperEnvironment() {
  return {
    PATH: process.env.PATH || "/usr/bin:/bin",
    LANG: "C.UTF-8",
    ...(process.platform === "win32"
      ? {
          SystemRoot: process.env.SystemRoot,
          WINDIR: process.env.WINDIR,
          TEMP: process.env.TEMP,
          TMP: process.env.TMP,
        }
      : {}),
  };
}
const clarifications = new ClarificationBroker({
  owner: (sessionId) => ownership.forSession(sessionId),
  emit: (type, value) => emit(type, value),
  addSource: (source) => {
    config.user_turns = [...config.user_turns, source].slice(-64);
  },
});
function redact(value) {
  let serialized = JSON.stringify(value);
  for (const secret of [config?.api_key, token, metadataAuth])
    if (secret) serialized = serialized.replaceAll(secret, "[REDACTED]");
  return serialized;
}
function emit(type, payload = {}) {
  process.stdout.write(
    redact({ sequence: ++sequence, type, ...payload }) + "\n",
  );
}
function declareDeliveryCheck(turnId, event, sessionId) {
  const owner = ownership.forTurn(turnId);
  const data = event?.data;
  if (owner?.id !== turnId || data?.name !== "delivery_check") return;
  let args = data.arguments;
  try {
    if (typeof args === "string") args = JSON.parse(args);
    args = checkArguments(args);
  } catch {
    return;
  }
  const key = createHash("sha256").update(args.command).digest("hex");
  if (owner.declaredChecks.size >= 128 && !owner.declaredChecks.has(key)) {
    owner.incompleteChecks = true;
    return;
  }
  const previous = owner.declaredChecks.get(key);
  const call = observationKey(sessionId, data.turn, data.step, data.callId);
  if (owner.declaredCheckCalls >= 4096 && !previous?.calls.has(call)) {
    owner.incompleteChecks = true;
    return;
  }
  const declared = previous || {
    check_key: key,
    check_label: "交付检查",
    state: "unavailable",
    exit_code: null,
    required: false,
    watch: [],
    inputs: [],
    calls: new Map(),
  };
  declared.required ||= args.required !== false;
  try {
    if (args.inputs)
      declared.inputs = mergeCheckpointPaths(declared.inputs, args.inputs);
    declared.watch = mergeCheckpointPaths(
      declared.watch,
      mergeCheckpointPaths(
        args.watch,
        declared.inputs.length ? declared.inputs : args.watch,
      ),
    );
  } catch {
    owner.incompleteChecks = true;
  }
  if (!declared.calls.has(call)) {
    owner.declaredCheckCalls++;
    declared.calls.set(call, {
      session_id: sessionId,
      sequence: event.seq,
      time: event.time,
    });
    declared.check_label =
      typeof args.label === "string" &&
      args.label.isWellFormed() &&
      args.label.trim() &&
      args.label.length <= 80 &&
      !/[\x00-\x1f]/.test(args.label)
        ? args.label
        : "交付检查";
  }
  owner.declaredChecks.set(key, declared);
}
function currentDeliveryReceipt(declaration, value) {
  if (!declaration) return true;
  const reference = declaration.calls.get(
    observationKey(
      value.session_id,
      value.sdk_turn,
      value.sdk_step,
      value.call_id,
    ),
  );
  if (!reference) return false;
  for (const candidate of declaration.calls.values()) {
    if (candidate === reference) continue;
    // Session sequence resolves same-millisecond calls within one agent.
    // Across agents an equal or unknown timestamp remains inconclusive.
    if (candidate.session_id === reference.session_id) {
      if (
        !Number.isSafeInteger(candidate.sequence) ||
        !Number.isSafeInteger(reference.sequence) ||
        candidate.sequence > reference.sequence
      )
        return false;
    } else if (
      !Number.isSafeInteger(candidate.time) ||
      !Number.isSafeInteger(reference.time) ||
      candidate.time >= reference.time
    )
      return false;
  }
  return true;
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
      declareDeliveryCheck(turnId, event, notification.params.sessionId);
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
        rememberJob(observation, turnId);
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
        if (notification.method === "subagent.started")
          ownership.bind(
            notification.params.parentSessionId,
            notification.params.childSessionId,
          );
        const owner =
          notification.method === "session.event"
            ? ownership.eventOwner(
                notification.params.sessionId,
                notification.params.event,
              )
            : ownership.forSession(
                notification.params?.sessionId ||
                  notification.params?.childSessionId,
              );
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
function control(method, path, options = {}) {
  // Both platforms reuse the already-owned SDK pipe; no extra listener or
  // host-to-container loopback capability is required for lifecycle control.
  return harness.client
    .request(
      "chengxin/control",
      {
        method,
        path,
        ...(options.scope ? { scope: options.scope } : {}),
        ...(options.admission ? { admission: options.admission } : {}),
      },
      7000,
    )
    .then((result) => {
      if (
        result?.status !== 200 ||
        typeof result.body !== "string" ||
        result.body.length > 512000
      )
        throw new Error("Runtime control failed");
      return result.body;
    });
}
async function capabilityRequest(request, response) {
  const owned = request.executionOwner;
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
  upstreamRequests.set(abort, owned);
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
          "-X",
          "utf8",
          "-c",
          "import sys;sys.path.insert(0,sys.argv[1]);from medagent.execution_capabilities import main;main()",
          join(integration, "../../src"),
        ],
        {
          cwd: integration,
          env: trustedHelperEnvironment(),
          windowsHide: true,
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
      ownership.forTurn(owned.id) !== owned ||
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
  const owned = request.executionOwner;
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
  upstreamRequests.set(abort, owned);
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
          "-X",
          "utf8",
          "-c",
          "import sys;sys.path.insert(0,sys.argv[1]);from medagent.execution_changes import main;main()",
          join(integration, "../../src"),
        ],
        {
          cwd: integration,
          env: trustedHelperEnvironment(),
          windowsHide: true,
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
      ownership.forTurn(owned.id) !== owned ||
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
async function workspaceCheckpoint(paths, owned) {
  paths = checkpointPaths(paths);
  if (
    !owned ||
    owned.cancelled ||
    ownership.forTurn(owned.id) !== owned ||
    Date.now() >= owned.deadline ||
    owned.checkpointCalls >= 256 ||
    checkpointLifetimeCalls >= 1024
  )
    throw new Error("文件版本核对当前不可用");
  owned.checkpointCalls++;
  checkpointLifetimeCalls++;
  const abort = new AbortController();
  upstreamRequests.set(abort, owned);
  const timeout = setTimeout(
    () => abort.abort(),
    Math.min(5000, Math.max(1, owned.deadline - Date.now())),
  );
  try {
    const result = await new Promise((resolve, reject) => {
      const child = execFile(
        config.python,
        [
          "-I",
          "-X",
          "utf8",
          "-c",
          "import sys;sys.path.insert(0,sys.argv[1]);from medagent.execution_checkpoints import main;main()",
          join(integration, "../../src"),
        ],
        {
          cwd: integration,
          env: trustedHelperEnvironment(),
          windowsHide: true,
          signal: abort.signal,
          timeout: 5000,
          maxBuffer: 16000,
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
          paths,
        }),
      );
    });
    if (
      owned.cancelled ||
      ownership.forTurn(owned.id) !== owned ||
      Date.now() >= owned.deadline ||
      !result.ok ||
      !validCheckpoint(result.result)
    )
      throw new Error(
        "文件版本核对未能完成: " +
          JSON.stringify({
            helper_ok: result.ok === true,
            complete: result.result?.complete,
            files: result.result?.files,
          }),
      );
    return result.result;
  } catch (error) {
    emit("workspace.checkpoint_unavailable", {
      turn_id: owned.id,
      diagnostic: String(error.message).slice(0, 1000),
    });
    throw error;
  } finally {
    clearTimeout(timeout);
    upstreamRequests.delete(abort);
  }
}
async function checkpointRequest(request, response) {
  try {
    const chunks = [];
    let bytes = 0;
    for await (const chunk of request) {
      bytes += chunk.length;
      if (bytes > 18000) throw new Error("文件版本参数过大");
      chunks.push(chunk);
    }
    const args = JSON.parse(Buffer.concat(chunks));
    if (!args || Object.keys(args).some((key) => key !== "paths"))
      throw new Error("文件版本参数无效");
    const result = await workspaceCheckpoint(
      args.paths,
      request.executionOwner,
    );
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(redact({ ok: true, result }));
  } catch {
    if (response.destroyed || response.writableEnded) return;
    response.writeHead(422, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ ok: false, error: "文件版本核对不可用" }));
  }
}
async function proxy(request, response) {
  const supplied =
    request.headers["x-api-key"] ||
    request.headers.authorization?.replace(/^Bearer /, "") ||
    "";
  const metadataRequest =
    request.method === "POST" && request.url === "/model/runtime/realpath";
  const expected = metadataRequest ? metadataAuth : token;
  if (
    Buffer.byteLength(supplied) !== expected.length ||
    !timingSafeEqual(Buffer.from(supplied), Buffer.from(expected))
  )
    return errorResponse(response, 401);
  if (metadataRequest) {
    if (!resolveNativePath) return errorResponse(response, 404);
    if (++metadataCalls > 65536) return errorResponse(response, 429);
    const chunks = [];
    let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > 131072) return errorResponse(response, 413);
      chunks.push(chunk);
    }
    let result;
    try {
      const value = JSON.parse(Buffer.concat(chunks));
      result =
        value &&
        Object.keys(value).every((key) => ["path", "operation"].includes(key))
          ? value.operation === "directory-stat"
            ? resolveNativePath.statDirectory(value.path)
            : !value.operation || value.operation === "realpath"
              ? resolveNativePath(value.path)
              : { ok: false, code: "EINVAL" }
          : { ok: false, code: "EINVAL" };
    } catch {
      result = { ok: false, code: "EINVAL" };
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(result));
    return;
  }
  if (request.method === "POST" && request.url === "/model/admission") {
    const sessionId = request.headers["x-chengxin-session-id"];
    if (sessionId !== config.id) return errorResponse(response, 400);
    const chunks = [];
    let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > 1024) return errorResponse(response, 413);
      chunks.push(chunk);
    }
    try {
      const value = JSON.parse(Buffer.concat(chunks));
      const owner = ownership.forTurn(value?.task_id);
      const admitted =
        owner &&
        !owner.cancelled &&
        Date.now() < owner.deadline &&
        Number.isSafeInteger(value?.turn) &&
        value.turn > 0 &&
        ownership.admitRootTurn(config.id, value.turn, owner);
      response.writeHead(admitted ? 200 : 409, {
        "Content-Type": "application/json",
      });
      response.end(JSON.stringify({ ok: Boolean(admitted) }));
    } catch {
      errorResponse(response, 400);
    }
    return;
  }
  if (
    request.method === "GET" &&
    request.url.startsWith("/model/admission/owner?")
  ) {
    const requester = request.headers["x-chengxin-session-id"];
    const url = new URL(request.url, "http://runtime.local");
    const sessionId = url.searchParams.get("session_id") || "";
    if (
      requester !== config.id ||
      sessionId.length > 256 ||
      /[\x00-\x20\x7f]/.test(sessionId)
    )
      return errorResponse(response, 400);
    const owner = ownership.forSession(sessionId);
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ task_id: owner?.id || null }));
    return;
  }
  const scoped = scopedModelRequest(request.url);
  if (scoped) {
    if (
      scoped.invalid ||
      (request.headers["x-chengxin-session-id"] &&
        request.headers["x-chengxin-session-id"] !== scoped.sessionId)
    )
      return errorResponse(response, 400);
    request.headers["x-chengxin-session-id"] = scoped.sessionId;
    request.url = scoped.path;
  }
  request.executionOwner = await ownership.forRequest(request.headers);
  if (
    (request.method === "GET" && request.url === "/model/capabilities") ||
    (request.method === "POST" && request.url === "/model/capabilities/execute")
  )
    return capabilityRequest(request, response);
  if (request.method === "POST" && request.url === "/model/workspace/changes")
    return workspaceRequest(request, response);
  if (
    request.method === "POST" &&
    request.url === "/model/workspace/checkpoint"
  )
    return checkpointRequest(request, response);
  if (request.method === "POST" && request.url === "/model/clarifications")
    return clarifications.request(request, response);
  if (request.method === "GET" && request.url === "/model/budget") {
    const budget = request.executionOwner;
    const usable = budget && !budget.cancelled && Date.now() < budget.deadline;
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        turn_id: budget?.id || null,
        deadline_at_ms: usable ? budget.deadline : null,
        remaining_calls: usable
          ? Math.max(
              0,
              Math.min(
                budget.maxCalls - budget.modelCalls,
                200 - lifetimeCalls,
              ),
            )
          : 0,
        remaining_seconds: usable
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
  const budget = request.executionOwner;
  if (
    !budget ||
    budget.cancelled ||
    Date.now() >= budget.deadline ||
    budget.modelCalls >= budget.maxCalls ||
    lifetimeCalls >= 200
  )
    return errorResponse(response, 429);
  // Reserve before await: root and concurrent descendants share a hard bound.
  budget.modelCalls++;
  if (budget === active || budget === lease) calls = budget.modelCalls;
  lifetimeCalls++;
  const reservedIndex = budget.modelCalls;
  const reservedLifetimeIndex = lifetimeCalls;
  const turnId = budget.id;
  emit("model.request_started", {
    turn_id: turnId,
    index: reservedIndex,
    lifetime_calls: reservedLifetimeIndex,
    session_id:
      request.headers["x-deepseek-harness-session-id"] ||
      request.headers["x-chengxin-session-id"] ||
      null,
    source: scoped ? "search" : "conversation",
  });
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
  if (budget.cancelled || Date.now() >= budget.deadline)
    return errorResponse(response, 429);
  const abort = new AbortController();
  upstreamRequests.set(abort, budget);
  const timeout = setTimeout(
    () => abort.abort(),
    Math.min(60000, Math.max(1, budget.deadline - Date.now())),
  );
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
      let emitLength = Math.max(0, pending.length - retain);
      // response.write encodes each string separately. Keep a surrogate pair
      // together when the redaction lookbehind ends inside an astral character.
      if (
        emitLength > 0 &&
        pending.charCodeAt(emitLength - 1) >= 0xd800 &&
        pending.charCodeAt(emitLength - 1) <= 0xdbff &&
        pending.charCodeAt(emitLength) >= 0xdc00 &&
        pending.charCodeAt(emitLength) <= 0xdfff
      )
        emitLength--;
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
  const native = process.platform === "win32";
  return new DeepSeekHarness({
    dshBin: join(integration, "isolated-entry.mjs"),
    profile: "sdk",
    patches: [
      native
        ? config.native_patch
        : "/opt/medical-agent/integrations/dsh/execution.patch.yml",
    ],
    dshHome: native ? join(config.state, "home") : "/state/home",
    processCwd: config.workspace,
    cwd: native ? config.workspace : "/workspace",
    provider: "deepseek-official",
    model: config.model,
    maxTokens: 8192,
    // A packaged ProcessContainer can need an extra few seconds for its first
    // native SDK startup on Windows (for example, while Windows scans the
    // bundled runtime). Keep this below the owner-side startup budget.
    initializeTimeoutMs: native ? 40000 : 20000,
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
      ...(native
        ? {
            SystemRoot: process.env.SystemRoot,
            TEMP: process.env.TEMP,
            TMP: process.env.TMP,
            CHENGXIN_PYTHON: config.python,
            CHENGXIN_NATIVE_PATCH: config.native_patch,
            CHENGXIN_PATH_BROKER: `http://127.0.0.1:${gateway.address().port}/model/runtime/realpath`,
            CHENGXIN_PATH_BROKER_AUTH: metadataAuth,
          }
        : {}),
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
  if (process.platform === "win32") {
    const patches = join(config.state, "..", ".native-patches");
    await mkdir(patches, { recursive: true });
    config.native_patch = join(patches, config.id + ".yml");
    let patch = await readFile(
      join(integration, "execution.patch.yml"),
      "utf8",
    );
    patch = patch
      .replace(
        /name: \/opt\/medical-agent\/integrations\/dsh\/([^\r\n]+)/g,
        (_all, name) => {
          const file =
            name === "execution-shell.mjs"
              ? join(integration, "../windows/powershell-executor.mjs")
              : join(integration, name);
          return "name: " + JSON.stringify(pathToFileURL(file).href);
        },
      )
      .replace(
        "workspaceRoot: /workspace",
        "workspaceRoot: " + JSON.stringify(config.workspace),
      )
      .replace(
        "Work in /workspace;",
        "Work in the selected Windows project directory;",
      )
      .replace(
        "The validated Linux environment provides python3; do not assume python or pip is installed.",
        "This is native Windows. The bash-named tool runs Windows PowerShell. Use PowerShell syntax and npm.cmd for npm. Each command uses a ChengxinWorkspace: PSDrive rooted at its workdir. Use relative paths for files and $PWD.ProviderPath when native programs need a physical path; the workdir tool parameter uses physical Windows paths. Python and pip are bundled; use python.exe -m venv .venv for project dependencies, then .venv/Scripts/python.exe -m pip. The bundled runtime is read-only; scientific packages are not preinstalled. Native command failures preserve their exit code.",
      )
      .replace(
        "Bash pipelines use pipefail by default.",
        "PowerShell errors stop the command and native exit codes are retained.",
      );
    // The selected workspace is the instruction boundary. Ancestors outside
    // that grant are intentionally inaccessible on native Windows.
    patch +=
      '\n- id: agent-instructions\n  config:\n    maxBytes: 65536\n    projectRootMarkers: ["."]\n';
    // The SDK normally switches to its pwsh tool/provider on Windows. This
    // adapter keeps the shared bash-named tool and supplies native PowerShell
    // through the same official job/result lifecycle, with one shell provider.
    patch +=
      "- id: pwsh-sandbox\n  disabled: true\n- id: tool-pwsh\n  disabled: true\n- id: tool-bash\n  disabled: false\n";
    await writeFile(config.native_patch, patch, { mode: 0o600 });
    resolveNativePath = createPathResolver(
      [
        config.workspace,
        config.state,
        join(integration, "../../node_modules"),
        dirname(integration),
        dirname(process.execPath),
        dirname(config.python),
        config.native_patch,
        join(integration, "../../package.json"),
        { path: join(integration, "../.."), recursive: false },
        join(process.env.SystemRoot, "System32/WindowsPowerShell/v1.0"),
      ],
      { ancestorMetadata: true },
    );
  }
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
    isolation:
      process.platform === "win32"
        ? "windows-processcontainer"
        : "bubblewrap-filesystem",
    network: "enabled",
    model: config.model,
  });
}
function rememberJob(observation, turnId) {
  if (
    !observation.job_id ||
    !["bash", "delivery_check"].includes(observation.tool)
  )
    return;
  // Reading another task's job cannot transfer ownership of that job.
  if (!jobOwners.has(observation.job_id)) {
    jobOwners.set(observation.job_id, {
      id: observation.job_id,
      session_id: observation.session_id,
      turn_id: turnId,
      epoch: observation.epoch,
    });
    if (jobOwners.size > 4096) jobOwners.delete(jobOwners.keys().next().value);
  }
}
function scopeFor(owned) {
  return {
    root: config.id,
    sessions: ownership.sessionsFor(owned),
    jobs: [...jobOwners.values()]
      .filter((j) => j.turn_id === owned.id && j.epoch === observationEpoch)
      .map(({ id, session_id }) => ({ id, session_id })),
  };
}
async function cancelGrant(owned) {
  if (owned.stopping) return owned.stopping;
  const wasActive = active === owned;
  const hadHostWork =
    [...upstreamRequests.values()].some((owner) => owner === owned) ||
    [...clarifications.pending.values()].some(
      (pending) => pending.owned === owned,
    );
  owned.cancelled = true;
  owned.abort?.abort();
  clearTimeout(owned.timer);
  clarifications.cancel(
    owned.id,
    owned.timedOut ? "time_budget_exhausted" : "turn_stopped",
  );
  for (const [abort, owner] of upstreamRequests)
    if (owner === owned) abort.abort();
  owned.stopping = (async () => {
    await inspect(true).catch(() => {});
    const scope = scopeFor(owned);
    const status = JSON.parse(await control("GET", "/status"));
    if (
      wasActive &&
      !owned.timedOut &&
      status.jobs.some(
        (job) =>
          job.session_id === config.id &&
          hasActiveJobs([job]) &&
          !jobOwners.has(job.id),
      )
    )
      throw new Error(
        "Cannot attribute an active root job during task failure",
      );
    const work =
      wasActive ||
      hadHostWork ||
      scope.sessions.some((id) => runningAgents.has(id)) ||
      status.jobs.some(
        (j) =>
          hasActiveJobs([j]) &&
          ((j.session_id !== config.id &&
            scope.sessions.includes(j.session_id)) ||
            scope.jobs.some(
              (ref) => ref.id === j.id && ref.session_id === j.session_id,
            )),
      );
    if (owned.timedOut && work && !owned.deadlineReported) {
      owned.deadlineReported = true;
      emit("turn.deadline", {
        turn_id: owned.id,
        time_budget_seconds: owned.timeBudgetSeconds,
      });
    }
    await control("POST", "/cancel", { scope });
    if (!wasActive && owned.timedOut && work && !owned.workStoppedReported) {
      owned.workStoppedReported = true;
      emit("turn.work_stopped", {
        turn_id: owned.id,
        reason: "time_budget_exhausted",
      });
    }
    if (lease === owned && active !== owned) lease = null;
  })().catch(async () => {
    // Failure to stop the selected work must still fail closed. This exceptional
    // fallback is observable and may interrupt other grants in this workspace.
    emit("runtime.control_failed", {
      turn_id: owned.id,
      reason: "Task cancellation failed; stopping the entire workspace.",
    });
    await cancel();
  });
  return owned.stopping;
}
async function cancel() {
  if (stopping) return stopping;
  if (!harness) return;
  const ownedTurn = active;
  const ownedBudget = active || lease;
  clarifications.cancel(
    null,
    ownedBudget?.timedOut ? "time_budget_exhausted" : "user_cancelled",
  );
  if (active || lease) {
    (active || lease).cancelled = true;
    (active || lease).abort?.abort();
  }
  ownership.cancelAll();
  for (const abort of upstreamRequests.keys()) abort.abort();
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
      jobOwners.clear();
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
      const turnId =
        callOwners.get(key) ||
        ownership.forSdkTurn(observation.session_id, observation.sdk_turn)?.id;
      observationSequence = observation.sequence;
      if (turnId) {
        rememberJob(observation, turnId);
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
  const agents = [...runningAgents].sort().map((session_id) => ({
    session_id,
    turn_id: ownership.forSession(session_id)?.id || null,
    status: "running",
  }));
  const snapshot = { jobs: status.jobs, agents };
  const signature = JSON.stringify(snapshot);
  if (signature !== lastJobs) {
    lastJobs = signature;
    emit("jobs.snapshot", snapshot);
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
    abort: new AbortController(),
    maxCalls: payload.max_calls,
    timeBudgetSeconds: seconds,
    modelCalls: 0,
    deadline: Date.now() + seconds * 1000,
    capabilityCalls: 0,
    workspaceCalls: 0,
    checkpointCalls: 0,
    declaredChecks: new Map(),
    declaredCheckCalls: 0,
    incompleteChecks: false,
  };
  ownership.remember(active);
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
    // A completed foreground turn can retain a child model request. Mark that
    // historical turn as stopping as soon as its own deadline arrives; the
    // asynchronous runtime control below then tears down the selected child.
    // This must not wait behind an unrelated foreground provider request.
    const backgroundWork =
      active !== owned &&
      ([...upstreamRequests.values()].some((owner) => owner === owned) ||
        ownership
          .sessionsFor(owned)
          .some((session) => runningAgents.has(session)) ||
        [...clarifications.pending.values()].some(
          (pending) => pending.owned === owned,
        ));
    if (backgroundWork) {
      owned.deadlineReported = true;
      owned.workStoppedReported = true;
      emit("turn.deadline", {
        turn_id: owned.id,
        time_budget_seconds: owned.timeBudgetSeconds,
      });
      emit("turn.work_stopped", {
        turn_id: owned.id,
        reason: "time_budget_exhausted",
      });
    }
    cancelGrant(owned).catch(() => {});
  }, seconds * 1000);
  owned.timer = timer;
  try {
    if (!harness) {
      harness = await makeHarness();
      await harness.start();
      observeRuntime();
    }
    const promptOwned = (text) =>
      runOwnedPrompt({
        client: harness.client,
        sessionId: config.id,
        text,
        taskId: owned.id,
        register: (taskId) =>
          control("POST", "/admission", { admission: { task_id: taskId } }),
        ownerForTurn: (turn) => ownership.forSdkTurn(config.id, turn)?.id,
        signal: owned.abort.signal,
      });
    const guidance = `\n\n[执行上下文] 本轮主任务、子智能体和辅助搜索合计最多 ${owned.maxCalls} 次模型请求；为实际运行、保存要求的产物和最终回复留出调用。批量读取相关文件，执行有针对性的检查；通过后及时收尾。简单任务无需额外委托。用户要求委托或任务需要分工时，提前安排范围明确的子任务；下一步依赖其结果时设置 run_in_background=false，避免父子重复检查与 sleep 轮询。`;
    let result = await promptOwned(payload.text + guidance);
    const collectDeclarations = (value) => {
      // The tree subscription can lag behind run(); canonical returned events
      // provide a second, idempotent source for root declarations.
      for (const event of value.events) {
        ownership.recordTurn(config.id, event.data?.turn, owned);
        if (event.type === "tool/call")
          declareDeliveryCheck(owned.id, event, config.id);
      }
    };
    collectDeclarations(result);
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
      result = await promptOwned(
        "[执行恢复] 上一次模型输出达到单次上限，原任务尚未完成。继续本轮最初的用户任务，已有文件和工具结果仍在当前会话。不要重做已完成的工作；按较小步骤调用工具、写入或修改必要文件并实际验证。不要长篇推演或增加任务外审计。共享调用预算与截止时间未重置；只允许这一次自动恢复，完成后简短报告真实结果。",
      );
      collectDeclarations(result);
    }
    // One repair of explicit acceptance checks, without renewing any budget.
    const failedChecks = async () => {
      const status = JSON.parse(await control("GET", "/status"));
      if (
        owned.incompleteChecks ||
        status.incomplete_delivery_turns?.includes(owned.id)
      )
        return [
          {
            check_label: "检查记录不完整",
            state: "unavailable",
            exit_code: null,
          },
        ];
      const latest = new Map(
        (status.delivery_checks || [])
          .filter((value) => value.check_turn_id === owned.id)
          .map((value) => [value.check_key, value]),
      );
      const required = new Map(
        [...owned.declaredChecks].filter(([, value]) => value.required),
      );
      for (const [key, value] of latest)
        if (value.check_required !== false) required.set(key, value);
      // A declared check can fail before it obtains a bound receipt. Absence
      // of a receipt is unknown, never evidence that the check passed.
      const unresolved = [];
      const snapshots = new Map();
      for (const [key, declaration] of new Map([...latest, ...required])) {
        const value = latest.get(key) || declaration;
        const inputs = owned.declaredChecks.get(key)?.inputs || [];
        const receiptCurrent =
          value.state === "completed" &&
          value.exit_code === 0 &&
          currentDeliveryReceipt(owned.declaredChecks.get(key), value) &&
          coversVerifiedInputs(value.check_inputs, inputs);
        let freshness = "unavailable";
        const checkpoint = value.check_snapshot;
        const watch = owned.declaredChecks.get(key)?.watch ||
          checkpoint?.paths || ["."];
        if (
          receiptCurrent &&
          validCheckpoint(checkpoint) &&
          checkpoint.complete &&
          coversCheckpointPaths(checkpoint.paths, watch)
        ) {
          const signature = JSON.stringify(checkpointPaths(checkpoint.paths));
          if (!snapshots.has(signature))
            snapshots.set(
              signature,
              workspaceCheckpoint(checkpoint.paths, owned).catch(() => null),
            );
          const current = await snapshots.get(signature);
          if (current?.complete)
            freshness =
              current.sha256 === checkpoint.sha256 ? "current" : "stale";
        }
        if (value.call_id)
          emit("delivery.freshness", {
            turn_id: owned.id,
            check_key: key,
            call_id: value.call_id,
            session_id: value.session_id,
            freshness,
            watch,
          });
        if (required.has(key) && (!receiptCurrent || freshness !== "current"))
          unresolved.push(
            receiptCurrent
              ? {
                  ...value,
                  state: freshness === "stale" ? "stale" : "unavailable",
                  watch,
                  inputs,
                }
              : value.state === "completed" && value.exit_code === 0
                ? { ...owned.declaredChecks.get(key), inputs }
                : { ...value, watch, inputs },
          );
      }
      return unresolved;
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
      result = await promptOwned(
        "[交付检查纠错] 本轮已声明的检查尚未解决：" +
          JSON.stringify(
            unresolved.map(
              ({
                check_label,
                check_key,
                state,
                exit_code,
                watch,
                inputs,
              }) => ({
                label: check_label,
                check_key,
                state,
                exit_code,
                watch,
                inputs,
              }),
            ),
          ) +
          "。检查命令与实际输出保留在当前会话。stale表示检查通过后依赖文件已改变，需用原命令和至少原watch范围复验；inputs是检查开始和结束都须保持不变的输入范围，必须保留并复验，不包含生成的输出。退出0或缺失inputs版本证明不能沿用旧通过。遵守原用户的文件保护、授权与任务范围；只要求检查报告时不得扩展为修改。先判断实现、测试预期或当前版本API是否有误，在授权范围内修正后用原delivery_check命令复验。不得用无关命令或缩小watch替代，也不能隐藏失败。若范围、预算或时间不足，请如实说明未完成。调用额度和截止时间未重置，只允许本次自动纠错。",
      );
      collectDeclarations(result);
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
    if (outcome.type === "turn.failed")
      await cancelGrant(owned).catch(() => {});
    await owned.stopping?.catch(() => {});
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
  for (const abort of upstreamRequests.keys()) abort.abort();
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
