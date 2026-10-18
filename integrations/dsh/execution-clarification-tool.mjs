import { defineTool } from "@deepseek-ai/dsh-tools";
import { sessionHeaders } from "./execution-ownership.mjs";
export const name = "chengxin-execution-clarification";
export const inject = ["tools"];
export function apply(ctx) {
  ctx.tools.register(
    defineTool({
      name: "request_clarification",
      description:
        "任务的必要输入缺失、或用户要求与现有行为/验收明确冲突时，用简短问题向用户核对。提供两至三个真正互斥的选项，或不提供选项。工具会等待用户真实回答；不能自行代选或通过反复调用猜答案。先检查已有信息，不对常规实现细节和已经授权的动作重复询问。等待期间仍计入本轮时限；停止后不可继续使用旧问题。",
      parameters: {
        question: {
          type: "string",
          required: true,
          description: "需要用户回答的单一问题，最多240字",
        },
        context: {
          type: "string",
          description:
            "可选，最多320字，说明缺什么或哪两项要求冲突，不输出推理长文",
        },
        options: {
          type: "array",
          items: { type: "string" },
          description:
            "可选，两至三个互斥的短标签，优先8–20字，严格不超过80字符。完整解释放context，勿塞入选项；用户也可自由输入。省略表示仅自由输入。",
        },
      },
      timeoutMs: 1820000,
      isConcurrencySafe: () => false,
      output: {
        schema: { type: "json" },
        render: (_args, value) => [
          { type: "text", text: JSON.stringify(value) },
        ],
      },
      execute: async (args, exec) => {
        const response = await fetch(
          process.env.DEEPSEEK_BASE_URL + "/clarifications",
          {
            method: "POST",
            headers: {
              "x-api-key": process.env.DEEPSEEK_API_KEY,
              "Content-Type": "application/json",
              ...sessionHeaders(exec.agent?.id),
            },
            body: JSON.stringify({ ...args, session_id: exec.agent?.id }),
            signal: AbortSignal.any([
              AbortSignal.timeout(1820000),
              exec.signal,
            ]),
            redirect: "error",
          },
        );
        const reply = await response.json();
        if (!response.ok || !reply.ok)
          throw new Error(reply.error?.message || "澄清未完成");
        return reply.result;
      },
    }),
  );
}
