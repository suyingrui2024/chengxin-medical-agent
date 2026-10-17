// Explicit delivery checks use the same official executor, jobs and cancellation.
import { defineTool } from "@deepseek-ai/dsh-tools";
import { createHash } from "node:crypto";

export const name = "chengxin-execution-delivery";
export const inject = ["tools", "jobs"];
const ACTIVE = new Set(["starting", "running", "stopping"]);

export function checkKey(command) {
  return createHash("sha256").update(command).digest("hex");
}

export function checkOutcome(job) {
  if (!job || job.kind !== "bash")
    return { state: "unavailable", exit_code: null };
  if (job.status === "completed") {
    const match = /^exit code: (-?\d+)(?:;|$)/.exec(job.detail || "");
    const code = match ? Number(match[1]) : null;
    return {
      state: Number.isSafeInteger(code) ? "completed" : "unavailable",
      exit_code: Number.isSafeInteger(code) ? code : null,
    };
  }
  return {
    state:
      job.status === "killed"
        ? "cancelled"
        : job.status === "failed"
          ? "failed"
          : "unavailable",
    exit_code: null,
  };
}

export function mergeCheckRequirement(previous, current) {
  return previous?.check_turn_id === current.check_turn_id &&
    previous?.check_key === current.check_key &&
    previous?.check_required === true &&
    current.check_required === false
    ? { ...current, check_required: true }
    : current;
}

export function apply(ctx) {
  const requiredChecks = new Map();
  ctx.tools.register(
    defineTool({
      name: "delivery_check",
      description:
        "执行检查命令，返回实际退出状态和输出，并保存记录。required默认true表示本次交付验收，未解决失败会阻止完成；新增测试应尽早运行、失败后修正并用同一命令复验。用户只要求检查和报告时设required=false，不自动扩展为代码修复；故意失败诊断也可用普通bash。同轮已声明为必需的同一命令不能降级。执行仍受原隔离、时限和取消控制。退出0只证明这条命令成功，不证明需求覆盖、测试预期正确或临床有效。",
      parameters: {
        label: {
          type: "string",
          required: true,
          description: "简短检查名称，最多80字符",
        },
        command: {
          type: "string",
          required: true,
          description:
            "按当前项目真实版本执行的验收命令，最多4096字符；同一命令复验替代其旧状态",
        },
        timeout_ms: {
          type: "integer",
          description: "可选，100–120000毫秒，默认60000；仍受本轮剩余时间限制",
        },
        required: {
          type: "boolean",
          description:
            "可选，默认true；只检查并报告时false，失败仍记录，但不触发纠错或阻止完成。不能降级本轮已必需的同一命令。",
        },
      },
      timeoutMs: 135000,
      isConcurrencySafe: () => false,
      output: {
        schema: { type: "json" },
        render: (_args, value) => [
          { type: "text", text: JSON.stringify(value) },
        ],
      },
      execute: async (args, exec) => {
        if (
          typeof args.label !== "string" ||
          !args.label.isWellFormed() ||
          !args.label.trim() ||
          args.label.length > 80 ||
          /[\x00-\x1f]/.test(args.label) ||
          typeof args.command !== "string" ||
          !args.command.isWellFormed() ||
          !args.command.trim() ||
          args.command.length > 4096 ||
          args.command.includes("\0") ||
          (args.required !== undefined && typeof args.required !== "boolean") ||
          (args.timeout_ms !== undefined &&
            (!Number.isInteger(args.timeout_ms) ||
              args.timeout_ms < 100 ||
              args.timeout_ms > 120000))
        )
          throw new Error("交付检查参数无效");
        const result = {
          schema: "execution-delivery/v1",
          label: args.label,
          check_key: checkKey(args.command),
          owner_turn_id: null,
          required: args.required !== false,
          state: "unavailable",
          exit_code: null,
          job_id: null,
          output: "",
        };
        const nested = async (name, input, suffix, signal = exec.signal) => {
          const value = await ctx.tools.execute({
            name,
            arguments: input,
            agent: exec.agent,
            parent: exec.token,
            callId: `${exec.callId}:check:${suffix}`,
            signal,
          });
          for (const context of value.additionalContexts || [])
            exec.deferContext(context);
          if (value.isError)
            throw new Error(value.error?.message || "检查工具执行失败");
          return value.value;
        };
        let timer;
        const deadline = new AbortController();
        try {
          const response = await fetch(
            process.env.DEEPSEEK_BASE_URL + "/budget",
            {
              headers: { "x-api-key": process.env.DEEPSEEK_API_KEY },
              signal: AbortSignal.any([exec.signal, AbortSignal.timeout(2000)]),
              redirect: "error",
            },
          );
          const budget = await response.json();
          if (
            !response.ok ||
            !/^[0-9a-f-]{36}$/.test(budget.turn_id || "") ||
            !(budget.remaining_seconds > 0)
          )
            throw new Error("检查无法绑定仍有效的任务预算");
          result.owner_turn_id = budget.turn_id;
          if (!requiredChecks.has(budget.turn_id)) {
            requiredChecks.set(budget.turn_id, new Set());
            if (requiredChecks.size > 64)
              requiredChecks.delete(requiredChecks.keys().next().value);
          }
          const declared = requiredChecks.get(budget.turn_id);
          if (result.required) {
            if (declared.size >= 128 && !declared.has(result.check_key))
              throw new Error("本轮必需检查登记已达上限");
            declared.add(result.check_key);
          }
          result.required = declared.has(result.check_key);
          const limit = Math.min(
            args.timeout_ms ?? 60000,
            budget.remaining_seconds * 1000,
          );
          const launched = await nested(
            "bash",
            {
              command: args.command,
              description: args.label,
              run_in_background: true,
            },
            "start",
          );
          if (
            !["background", "promoted"].includes(launched?.kind) ||
            typeof launched.jobId !== "string"
          )
            throw new Error("检查命令未获得可管理的后台任务");
          result.job_id = launched.jobId;
          timer = setTimeout(() => deadline.abort(), limit);
          const value = await nested(
            "job_output",
            { job_id: result.job_id, wait: true, timeout_ms: limit },
            "wait",
            AbortSignal.any([exec.signal, deadline.signal]),
          );
          Object.assign(result, checkOutcome(value?.job));
          result.output =
            typeof value?.text === "string"
              ? Array.from(value.text).slice(-16000).join("")
              : "";
          if (ACTIVE.has(value?.job?.status)) result.state = "timed_out";
        } catch (error) {
          result.state = exec.signal.aborted
            ? "cancelled"
            : deadline.signal.aborted
              ? "timed_out"
              : "failed";
          result.output = String(error.message).slice(0, 1000);
        } finally {
          clearTimeout(timer);
          if (result.job_id) {
            try {
              const job = ctx.jobs.get(result.job_id, exec.agent.id);
              if (job && ACTIVE.has(job.status)) {
                ctx.jobs.kill(
                  result.job_id,
                  exec.agent.id,
                  "Delivery check stopped",
                );
                await ctx.jobs.wait(result.job_id, 5000, exec.agent.id);
              }
            } catch {
              result.state = "failed";
              result.exit_code = null;
              result.output += "\n检查进程回收未获确认。";
            }
          }
        }
        if (requiredChecks.get(result.owner_turn_id)?.has(result.check_key))
          result.required = true;
        return result;
      },
    }),
  );
}
