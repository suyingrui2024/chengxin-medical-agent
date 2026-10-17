// Display only typed research observations; text printed by commands is not status.
export const toolObservationKey = (session, turn, step, call) =>
  `${session}:${turn}:${step}:${call}`;
export function observationPresentation(value) {
  let kind = "model",
    title = "运行状态未提供";
  if (value.schema !== "execution-observation/v1")
    return { kind: "model", title };
  if (value.tool === "delivery_check") {
    const label = value.check_label || "交付检查";
    if (value.state === "completed" && value.exit_code === 0)
      return { kind: "result", title: `${label} · 命令退出 0`, symbol: "✓" };
    return {
      kind: "failure",
      title: `${label} · ${value.state === "timed_out" ? "检查超时" : value.state === "cancelled" ? "检查已停止" : value.exit_code !== null ? `命令退出 ${value.exit_code}` : "检查未完成"}`,
    };
  }
  if (value.exit_code !== null && value.exit_code !== 0) {
    kind = "failure";
    title = `${value.tool === "job_output" ? "后台命令退出" : "命令退出"} · ${value.exit_code}`;
  } else if (value.state === "failed") {
    kind = "failure";
    title = "工具执行出错";
  } else if (value.state === "timed_out") {
    kind = "failure";
    title = "命令等待超时";
  } else if (["killed", "cancelled"].includes(value.state)) {
    kind = "failure";
    title = value.signal ? `命令已停止 · ${value.signal}` : "任务已停止";
  } else if (["promoted", "background"].includes(value.state)) {
    kind = "waiting";
    title = value.state === "promoted" ? "命令已转入后台" : "后台任务已启动";
  } else if (["starting", "running", "stopping"].includes(value.state)) {
    kind = "waiting";
    title =
      value.state === "starting"
        ? "后台任务正在准备"
        : value.state === "stopping"
          ? "后台任务正在停止"
          : "后台任务运行中";
  } else if (value.state === "completed") {
    kind = "result";
    title =
      value.tool === "job_output"
        ? "后台任务已完成"
        : `命令退出 · ${value.exit_code ?? "未知"}`;
  }
  return { kind, title };
}
