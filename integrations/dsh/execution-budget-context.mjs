import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { sessionHeaders } from "./execution-ownership.mjs";

// SDK assembly precedes pre-step admission. Query after admission, otherwise
// a new foreground turn would still display the previous task's expired budget.
export function registerBudgetContext(ctx) {
  ctx.on("agent/pre-step", async (payload, next) => {
    const decision = await next();
    if (decision.kind === "reject") return decision;
    try {
      const response = await fetch(process.env.DEEPSEEK_BASE_URL + "/budget", {
        headers: {
          "x-api-key": process.env.DEEPSEEK_API_KEY,
          ...sessionHeaders(payload.agent.id),
        },
        signal: AbortSignal.any([AbortSignal.timeout(2000), payload.signal]),
        redirect: "error",
      });
      if (!response.ok) return decision;
      const budget = await response.json();
      const text = `本轮全体智能体共享剩余 ${budget.remaining_calls} 次模型调用（包括即将进行的调用），剩余执行时间约 ${budget.remaining_seconds} 秒。先完成要求的代码、运行和产物；已有检查通过后直接收尾，不额外创建复核任务或长篇报告。用户要求的子任务应提前安排并明确范围，依赖结果时用返回的 ID 调用 wait_subagent，确认 state 与实际产物；send_message 送达后也需等待接续。剩余不足 8 次时不再启动可选子任务；不足 4 次时只处理已发现的必要问题或简短收尾，不重复读取与检查。长命令交还 job id 后读取同一任务，不要重新启动；一次等待不应超出剩余时间。相关检查已经完成时，停止无关或停滞的后台检查后收尾，保留其失败或未验证说明。不要用 sleep 轮询等待子任务。`;
      return {
        ...decision,
        messages: [
          ...decision.messages,
          createUserMessage({
            content: [{ type: "text", text }],
            source: {
              kind: "chengxin-execution-budget",
              form: "notice",
              summary: "当前任务共享预算",
            },
          }),
        ],
      };
    } catch {
      // The gateway still enforces limits when advisory context is unavailable.
      return decision;
    }
  });
}
