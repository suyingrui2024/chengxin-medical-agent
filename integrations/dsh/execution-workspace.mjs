// Research file observations are host-generated; they are not clinical receipts.
import { defineTool } from "@deepseek-ai/dsh-tools";
import { sessionHeaders } from "./execution-ownership.mjs";
export const name = "chengxin-execution-workspace";
export const inject = ["tools"];
export function apply(ctx) {
  ctx.tools.register(
    defineTool({
      name: "workspace_changes",
      description:
        "核对相对本轮开始的真实文件变化，无需 Git。默认返回前50项及分类计数；offset/limit 可分页。可用 path 查看有限文本差异。扫描不完整或文件不可核对时会明确标记，不能据此声称全部检查通过。文件变化可能包含子任务或本轮期间的其他写入；不会自动删除任何文件，也不写入患者记录。",
      parameters: {
        path: {
          type: "string",
          description: "可选，工作区内相对文件路径，查看该文件的前后差异",
        },
        offset: {
          type: "integer",
          description: "可选，下一页的 next_offset",
        },
        limit: {
          type: "integer",
          description: "可选，每页1–200项，默认50",
        },
      },
      output: {
        schema: { type: "json" },
        render: (_args, value) => [
          { type: "text", text: JSON.stringify(value) },
        ],
      },
      execute: async (args, exec) => {
        const response = await fetch(
          process.env.DEEPSEEK_BASE_URL + "/workspace/changes",
          {
            method: "POST",
            headers: {
              "x-api-key": process.env.DEEPSEEK_API_KEY,
              "Content-Type": "application/json",
              ...sessionHeaders(exec.agent?.id),
            },
            body: JSON.stringify(args),
            signal: AbortSignal.any([
              AbortSignal.timeout(12000),
              ...(exec.signal ? [exec.signal] : []),
            ]),
            redirect: "error",
          },
        );
        const reply = await response.json();
        if (!response.ok || !reply.ok)
          throw new Error(reply.error?.message || "无法核对文件变化");
        return reply.result;
      },
    }),
  );
}
