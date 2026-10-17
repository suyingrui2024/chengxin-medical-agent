// Registered tools call a host dispatcher; model code never runs in that host.
import { defineTool } from "@deepseek-ai/dsh-tools";
export const name = "chengxin-execution-capabilities";
export const inject = ["tools"];
export function apply(ctx) {
  const invoke = async (operation, args, exec) => {
    const response = await fetch(
      process.env.DEEPSEEK_BASE_URL +
        "/capabilities" +
        (operation === "execute" ? "/execute" : ""),
      {
        method: operation === "execute" ? "POST" : "GET",
        headers: {
          "x-api-key": process.env.DEEPSEEK_API_KEY,
          "Content-Type": "application/json",
        },
        body: operation === "execute" ? JSON.stringify(args) : undefined,
        signal: AbortSignal.any([
          AbortSignal.timeout(10000),
          ...(exec?.signal ? [exec.signal] : []),
        ]),
        redirect: "error",
      },
    );
    const value = await response.json();
    if (!response.ok || !value.ok)
      throw new Error(
        `${value.error?.code || "capability_transport_failed"}: ${value.error?.message || "注册计算不可用"}`,
      );
    return value.result;
  };
  const output = {
    schema: { type: "json" },
    render: (_args, value) => [{ type: "text", text: JSON.stringify(value) }],
  };
  ctx.tools.register(
    defineTool({
      name: "medical_capabilities",
      description:
        "查询此工作区固定版本的受信计算能力、输入契约及限制。先查询再调用 medical_calculate。返回错误时不能虚构能力或计算成功。",
      parameters: {},
      output,
      execute: (args, exec) => invoke("catalog", args, exec),
    }),
  );
  ctx.tools.register(
    defineTool({
      name: "medical_calculate",
      description:
        "执行目录内的固定公式，返回宿主计算回执。input 必须符合目录契约；evidence 必须逐字引用实际用户原文且包含数值和原单位。缺输入先补问；量纲错误按工具返回修正。结果仍待核对，不授权治疗或写入病例。任意脚本不具有此回执。",
      parameters: {
        capability_id: {
          type: "string",
          required: true,
          description: "能力目录中的 ID",
        },
        input: {
          type: "object",
          additionalProperties: true,
          required: true,
          description: "按目录 input_schema 填写结构化参数，数值类型也必须匹配",
        },
        evidence: {
          type: "string",
          required: true,
          description: "实际用户原文中的连续引文，包含输入数值和原单位",
        },
      },
      output,
      execute: (args, exec) => invoke("execute", args, exec),
    }),
  );
}
