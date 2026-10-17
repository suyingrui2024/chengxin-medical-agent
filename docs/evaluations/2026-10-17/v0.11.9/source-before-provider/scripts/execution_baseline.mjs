// Evaluation-only direct official SDK client. No product ExecutionManager/worker.
import { DeepSeekHarness } from "@deepseek-ai/dsh-sdk-client";
import { createInterface } from "node:readline";
import { writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve, join } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const lines = createInterface({ input: process.stdin });
let harness,
  configuration,
  running = false,
  closing = false;
function emit(type, fields = {}) {
  let value = JSON.stringify({ type, ...fields });
  if (configuration?.token)
    value = value.replaceAll(configuration.token, "[REDACTED]");
  process.stdout.write(value + "\n");
}
const patch = `
- id: session-log-deepseek
  disabled: true
- id: plugin-package-inventory-deepseek
  disabled: true
- id: anonymous-user-id
  disabled: true
- id: llm-retry
  disabled: true
- id: sandbox-policy
  config:
    mode: danger-full-access
    workspaceRoot: /workspace
- id: approval
  config:
    policy: never
- id: sdk-jsonrpc-server
  config:
    maxTokensAsSuccess: false
- id: web-search-deepseek
  config:
    baseURL: !!js process.env.DEEPSEEK_BASE_URL
    model: !!js process.env.CHENGXIN_MODEL
    maxUses: 3
    maxTokens: 2048
- id: tool-subagent
  config:
    provider: spawn
    toolName: subagent
    backgroundMode: continuable
    maxDepth: 2
- id: tool-subagent-fork
  config:
    provider: fork
    toolName: subagent_fork
    backgroundMode: one-shot
    maxDepth: 2
`;
async function configure(config) {
  if (configuration) throw new Error("Already configured");
  configuration = config;
  await mkdir(join(config.state, "home"), { recursive: true });
  await writeFile(join(config.state, "baseline.patch.yml"), patch);
  harness = new DeepSeekHarness({
    dshBin: join(root, "integrations/dsh/isolated-entry.mjs"),
    profile: "sdk",
    patches: ["/state/baseline.patch.yml"],
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
      ...Object.fromEntries(
        [
          "PATH",
          "HTTP_PROXY",
          "HTTPS_PROXY",
          "http_proxy",
          "https_proxy",
          "NO_PROXY",
        ]
          .filter((key) => process.env[key])
          .map((key) => [key, process.env[key]]),
      ),
      CHENGXIN_WORKSPACE: config.workspace,
      CHENGXIN_STATE: config.state,
      CHENGXIN_MODEL: config.model,
      DEEPSEEK_API_KEY: config.token,
      DEEPSEEK_BASE_URL: config.endpoint,
    },
  });
  await harness.start();
  emit("ready", { version: "0.2.0-rc.2" });
}
async function run(payload) {
  if (!harness || running || closing) throw new Error("Not ready");
  running = true;
  const subscription = harness.client.subscribeSessionTree(configuration.id);
  const notifications = [];
  const observer = (async () => {
    for await (const notification of subscription) {
      if (notification.method.startsWith("subagent."))
        notifications.push({
          method: notification.method,
          params: {
            parentSessionId: notification.params.parentSessionId,
            childSessionId: notification.params.childSessionId,
          },
        });
    }
  })();
  let output;
  try {
    const result = await harness.run(payload.text, {
      sessionId: configuration.id,
    });
    const reason = result.events.filter((e) => e.type === "turn/end").at(-1)
      ?.data?.reason;
    output = {
      status:
        reason?.kind === "completed" && result.finalResponse.trim()
          ? "completed"
          : "failed",
      final_response: result.finalResponse,
      reason,
      events: result.events.filter((event) =>
        ["tool/call", "tool/result", "turn/start", "turn/end"].includes(
          event.type,
        ),
      ),
      notifications,
    };
  } catch (error) {
    output = {
      status: "failed",
      error_type: error.constructor.name,
      notifications,
    };
  } finally {
    subscription.close();
    await observer.catch(() => {});
    running = false;
  }
  emit("result", output);
}
async function close() {
  if (closing) return;
  closing = true;
  await harness?.close();
  lines.close();
}
lines.on("line", (line) => {
  let payload;
  try {
    payload = JSON.parse(line);
  } catch {
    emit("error", { error_type: "InvalidJSON" });
    return;
  }
  const operation =
    payload.method === "configure"
      ? configure(payload)
      : payload.method === "run"
        ? run(payload)
        : payload.method === "close"
          ? close()
          : Promise.reject(new Error("Unknown operation"));
  operation.catch((error) =>
    emit("error", {
      error_type: error.constructor.name,
      diagnostic: String(error.message).slice(0, 2000),
    }),
  );
});
lines.on("close", () => {
  if (!closing) close().catch(() => {});
});
for (const signal of ["SIGTERM", "SIGINT"])
  process.on(signal, () => close().catch(() => {}));
