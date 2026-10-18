// Explicit delivery checks use the same official executor, jobs and cancellation.
import { defineTool } from "@deepseek-ai/dsh-tools";
import { createHash } from "node:crypto";
import {
  mergeCheckpointPaths,
  coversCheckpointPaths,
  validCheckpoint,
} from "./execution-checkpoints.mjs";
import {
  checkArguments,
  MAX_CHECK_TIMEOUT_MS,
} from "./execution-delivery-contract.mjs";

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
  const watchedChecks = new Map();
  const inputChecks = new Map();
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
          description:
            "可选，100–1800000毫秒，默认60000；实际取本轮剩余时间与此值中的较小者。长检查持续读取同一后台任务，可随时停止。",
        },
        required: {
          type: "boolean",
          description:
            "可选，默认true；只检查并报告时false，失败仍记录，但不触发纠错或阻止完成。不能降级本轮已必需的同一命令。",
        },
        watch: {
          type: "array",
          items: { type: "string" },
          description:
            '检查依赖的1至16项工作区相对文件或目录，默认["."]覆盖工作区（常见缓存目录除外）。检查结束记录文件版本，交付前变更会要求复验。同命令范围只能扩展。报告等输出不属于检查输入时可显式指定代码、测试和配置范围。',
        },
        inputs: {
          type: "array",
          items: { type: "string" },
          description:
            "检查必须保持不变的1至16项输入文件／目录。代码测试应声明代码、测试及配置；开始前和结束后核对输入版本，变化或核对失败不能沿用通过。不要包含生成的报告／结果文件，它们由watch绑定结束版本。同命令的inputs范围只能扩展，省略也不会取消此前声明。不设置inputs时只核对结束快照，不能证明执行期间输入稳定。",
        },
      },
      timeoutMs: MAX_CHECK_TIMEOUT_MS + 15000,
      isConcurrencySafe: () => false,
      output: {
        schema: { type: "json" },
        render: (_args, value) => [
          { type: "text", text: JSON.stringify(value) },
        ],
      },
      execute: async (args, exec) => {
        args = checkArguments(args);
        const watch = args.watch;
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
          checkpoint: null,
          wait_cycles: 0,
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
            watchedChecks.set(budget.turn_id, new Map());
            inputChecks.set(budget.turn_id, new Map());
            if (requiredChecks.size > 64) {
              const oldest = requiredChecks.keys().next().value;
              requiredChecks.delete(oldest);
              watchedChecks.delete(oldest);
              inputChecks.delete(oldest);
            }
          }
          const declared = requiredChecks.get(budget.turn_id);
          if (result.required) {
            if (declared.size >= 128 && !declared.has(result.check_key))
              throw new Error("本轮必需检查登记已达上限");
            declared.add(result.check_key);
          }
          result.required = declared.has(result.check_key);
          const watched = watchedChecks.get(budget.turn_id);
          const inputs = inputChecks.get(budget.turn_id);
          if (args.inputs)
            inputs.set(
              result.check_key,
              mergeCheckpointPaths(inputs.get(result.check_key), args.inputs),
            );
          const stableInputs = inputs.get(result.check_key) || [];
          watched.set(
            result.check_key,
            mergeCheckpointPaths(
              watched.get(result.check_key),
              mergeCheckpointPaths(
                watch,
                stableInputs.length ? stableInputs : watch,
              ),
            ),
          );
          const readCheckpoint = async (paths) => {
            const response = await fetch(
              process.env.DEEPSEEK_BASE_URL + "/workspace/checkpoint",
              {
                method: "POST",
                headers: {
                  "x-api-key": process.env.DEEPSEEK_API_KEY,
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({ paths }),
                signal: AbortSignal.any([
                  exec.signal,
                  AbortSignal.timeout(6000),
                ]),
                redirect: "error",
              },
            );
            const reply = await response.json();
            if (
              !response.ok ||
              !reply.ok ||
              !validCheckpoint(reply.result) ||
              !coversCheckpointPaths(reply.result.paths, paths)
            )
              throw new Error("文件版本未能核对");
            return reply.result;
          };
          let beforeInputs = null;
          if (stableInputs.length) {
            result.inputs_verified = [];
            try {
              beforeInputs = await readCheckpoint(stableInputs);
            } catch {}
          }
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
          const expires = Date.now() + limit;
          let value,
            iteration = 0;
          do {
            result.wait_cycles = iteration + 1;
            value = await nested(
              "job_output",
              {
                job_id: result.job_id,
                wait: true,
                timeout_ms: Math.min(600000, Math.max(1, expires - Date.now())),
              },
              `wait:${++iteration}`,
              AbortSignal.any([exec.signal, deadline.signal]),
            );
          } while (ACTIVE.has(value?.job?.status) && Date.now() < expires);
          Object.assign(result, checkOutcome(value?.job));
          result.output =
            typeof value?.text === "string"
              ? Array.from(value.text).slice(-16000).join("")
              : "";
          if (ACTIVE.has(value?.job?.status)) result.state = "timed_out";
          if (result.state === "completed" && result.exit_code === 0) {
            try {
              result.checkpoint = await readCheckpoint(
                watched.get(result.check_key),
              );
            } catch {
              result.output += "\n文件版本核对未完成，需再次复验。";
            }
            const needed = inputs.get(result.check_key) || [];
            if (needed.length) {
              result.inputs_verified = [];
              try {
                const afterInputs = await readCheckpoint(needed);
                if (
                  beforeInputs?.complete &&
                  afterInputs.complete &&
                  coversCheckpointPaths(beforeInputs.paths, needed) &&
                  JSON.stringify(beforeInputs.paths) ===
                    JSON.stringify(afterInputs.paths) &&
                  beforeInputs.sha256 === afterInputs.sha256
                )
                  result.inputs_verified = afterInputs.paths;
                else
                  result.output +=
                    "\n检查输入在运行期间变化或未能完整核对；退出0仍保留，当前结果不能证明最终输入已通过。请稳定输入后用同一命令及至少原inputs范围复验。";
              } catch {
                result.output +=
                  "\n检查输入版本未能核对，需在相同inputs范围复验。";
              }
            }
          }
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
