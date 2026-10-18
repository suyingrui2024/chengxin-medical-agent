// Pin a process to the host deadline at launch, independently of later turns.
// The original resolved spec remains the tool's foreground waiting bound.
import { sessionHeaders } from "./execution-ownership.mjs";
export async function taskBoundSpec(spec) {
  const response = await fetch(process.env.DEEPSEEK_BASE_URL + "/budget", {
    headers: {
      "x-api-key": process.env.DEEPSEEK_API_KEY,
      ...sessionHeaders(spec.dshEnv?.DSH_SESSION_ID),
    },
    signal: AbortSignal.any([
      AbortSignal.timeout(2000),
      ...(spec.signal ? [spec.signal] : []),
    ]),
    redirect: "error",
  });
  const budget = await response.json();
  const remaining = budget?.deadline_at_ms - Date.now();
  if (
    !response.ok ||
    !/^[0-9a-f-]{36}$/.test(budget?.turn_id || "") ||
    !Number.isSafeInteger(budget?.deadline_at_ms) ||
    remaining <= 0 ||
    remaining > 1800000
  )
    throw new Error("命令无法绑定仍有效的任务时限");
  return {
    ...spec,
    timeoutMs:
      spec.onExpiry === "kill"
        ? Math.min(spec.timeoutMs, remaining)
        : remaining,
    onExpiry: "kill",
  };
}
