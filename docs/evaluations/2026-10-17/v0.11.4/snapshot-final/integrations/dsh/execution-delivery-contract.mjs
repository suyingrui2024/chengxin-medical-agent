// Accepted execution inputs share one contract with the host completion gate.
import { checkpointPaths } from "./execution-checkpoints.mjs";

export const MAX_CHECK_TIMEOUT_MS = 1_800_000;
export function checkArguments(args) {
  const invalid = (detail) => {
    throw new Error(`交付检查参数无效：${detail}。命令尚未执行，请修正输入。`);
  };
  if (!args || typeof args !== "object" || Array.isArray(args))
    invalid("需要结构化参数");
  if (
    Object.keys(args).some(
      (name) =>
        !["label", "command", "required", "timeout_ms", "watch"].includes(name),
    )
  )
    invalid("字段须为label、command、required、timeout_ms、watch");
  if (
    typeof args.label !== "string" ||
    !args.label.isWellFormed() ||
    !args.label.trim() ||
    args.label.length > 80 ||
    /[\x00-\x1f]/.test(args.label)
  )
    invalid("label须为1至80字符的单行名称");
  if (
    typeof args.command !== "string" ||
    !args.command.isWellFormed() ||
    !args.command.trim() ||
    args.command.length > 4096 ||
    args.command.includes("\0")
  )
    invalid("command须为1至4096字符且不含空字符");
  if (args.required !== undefined && typeof args.required !== "boolean")
    invalid("required须为布尔值");
  if (
    args.timeout_ms !== undefined &&
    (!Number.isInteger(args.timeout_ms) ||
      args.timeout_ms < 100 ||
      args.timeout_ms > MAX_CHECK_TIMEOUT_MS)
  )
    invalid(`timeout_ms须为100至${MAX_CHECK_TIMEOUT_MS}的整数`);
  let watch;
  try {
    watch = checkpointPaths(args.watch);
  } catch (error) {
    invalid(error.message);
  }
  return {
    label: args.label,
    command: args.command,
    required: args.required !== false,
    timeout_ms: args.timeout_ms ?? 60000,
    watch,
  };
}
