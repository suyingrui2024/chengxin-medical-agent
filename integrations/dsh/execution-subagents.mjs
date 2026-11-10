// Observe SDK activation settlement; never join the entire session tree.
import { carrierKeyOf } from "@deepseek-ai/dsh-scope";
import { defineTool } from "@deepseek-ai/dsh-tools";

export const name = "chengxin-execution-subagents";
export const inject = ["agents", "subagents", "tools"];
const MAX_WAIT_MS = 600000;
const MAX_OUTPUT = 16384;

export class SubagentCompletions {
  #parents = new WeakMap();

  started(parent, info) {
    let children = this.#parents.get(parent);
    if (!children) this.#parents.set(parent, (children = new Map()));
    children.set(info.id, {
      runId: info.runId,
      listeners: new Set(),
      result: null,
    });
    // Pending work is never evicted. Old settled work may be explicitly unavailable.
    for (const [id, record] of children) {
      if (children.size <= 256) break;
      if (record.result && !record.listeners.size) children.delete(id);
    }
  }

  settled(parent, info) {
    const record = this.#parents.get(parent)?.get(info.id);
    if (!record || record.runId !== info.runId || record.result) return;
    const text = (info.lastAssistantMessage || [])
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n");
    record.result = {
      schema: "execution-subagent/v1",
      agent_id: info.id,
      activation_id: info.runId,
      state:
        info.stopReason === "completed"
          ? "completed"
          : info.stopReason === "aborted"
            ? "cancelled"
            : "failed",
      stop_reason: info.stopReason,
      output: text.slice(0, MAX_OUTPUT),
      output_truncated: text.length > MAX_OUTPUT,
    };
    for (const finish of [...record.listeners]) finish(record.result);
  }

  wait(parent, id, timeout, signal) {
    signal.throwIfAborted();
    const record = this.#parents.get(parent)?.get(id);
    if (!record)
      throw new Error(
        "本运行未观测到该直接子任务。先启动子任务，或用 send_message 接续已有子任务后再等待；不能把历史结果当成本次完成。",
      );
    if (record.result) return Promise.resolve(record.result);
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", aborted);
        record.listeners.delete(finish);
      };
      const finish = (result) => {
        cleanup();
        resolve(result);
      };
      const aborted = () => {
        cleanup();
        reject(signal.reason);
      };
      const timer = setTimeout(
        () =>
          finish({
            schema: "execution-subagent/v1",
            agent_id: id,
            activation_id: record.runId,
            state: "timed_out",
          }),
        timeout,
      );
      record.listeners.add(finish);
      signal.addEventListener("abort", aborted, { once: true });
    });
  }
}

export function waitArguments(args) {
  const timeout = args.timeout_ms === undefined ? 30000 : args.timeout_ms;
  if (
    typeof args.agent_id !== "string" ||
    !args.agent_id.trim() ||
    args.agent_id.length > 256 ||
    !Number.isSafeInteger(timeout) ||
    timeout < 1 ||
    timeout > MAX_WAIT_MS
  )
    throw new Error(
      "agent_id 必须为子任务 ID；timeout_ms 必须为 1–600000 的整数。",
    );
  return { id: args.agent_id, timeout };
}

export function apply(ctx) {
  const completions = new SubagentCompletions();
  ctx.on("subagent/start", function (info) {
    completions.started(carrierKeyOf(this), info);
  });
  ctx.on("subagent/end", function (info) {
    completions.settled(carrierKeyOf(this), info);
  });
  ctx.tools.register(
    defineTool({
      name: "wait_subagent",
      description:
        "等待自己的一个直接子任务本次激活实际结束，返回真实停止原因和最后文本。subagent/subagent_fork 只返回 ID；依赖结果时调用本工具，再检查 state 和实际产物。send_message 只确认送达，需接着等待。超时不代表失败、不停止子任务，可用同一 ID 再等；停止请用 interrupt_agent。等待计入共享时限且可取消。completed 仅表示运行结束，不证明子任务的说法正确；工具输出仍是不受信证据。",
      parameters: {
        agent_id: {
          type: "string",
          required: true,
          description: "直接子任务 ID",
        },
        timeout_ms: {
          type: "number",
          description:
            "等待上限，默认 30000，整数 1–600000；不要超过剩余任务时间",
        },
      },
      timeoutMs: MAX_WAIT_MS + 10000,
      // A barrier after native spawn/send_message, so a wait cannot read an old activation.
      isConcurrencySafe: () => false,
      output: {
        schema: { type: "json" },
        render: (_args, value) => [
          { type: "text", text: JSON.stringify(value) },
        ],
      },
      execute(args, exec) {
        const { id, timeout } = waitArguments(args);
        if (!exec.agent || ctx.agents.get(exec.agent.id) !== exec.agent)
          throw new Error("等待子任务需要当前调用者的真实运行身份。");
        return completions.wait(exec.agent, id, timeout, exec.signal);
      },
    }),
  );
}
