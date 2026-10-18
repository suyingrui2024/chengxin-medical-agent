import { ACTIVE_JOB_STATES } from "./execution-observations.mjs";

const identity = (value) =>
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= 256 &&
  !/[\x00-\x1f]/.test(value);

// Undefined is the explicit workspace stop. An empty or malformed scope must
// never become a workspace stop. This endpoint is private to the SDK pipe.
export function validCancellationScope(scope) {
  return (
    scope !== null &&
    typeof scope === "object" &&
    !Array.isArray(scope) &&
    Object.keys(scope).every((k) => ["root", "sessions", "jobs"].includes(k)) &&
    identity(scope.root) &&
    Array.isArray(scope.sessions) &&
    scope.sessions.length <= 4097 &&
    scope.sessions.every(identity) &&
    Array.isArray(scope.jobs) &&
    scope.jobs.length <= 4096 &&
    scope.jobs.every(
      (j) =>
        j &&
        Object.keys(j).every((k) => ["id", "session_id"].includes(k)) &&
        identity(j.id) &&
        identity(j.session_id),
    )
  );
}

export async function cancelRuntime(ctx, scope) {
  if (scope !== undefined && !validCancellationScope(scope))
    return { status: 400, body: "Invalid cancellation scope" };
  const agents = ctx.agents.list();
  const selected = scope && new Set(scope.sessions);
  const jobs = [];
  const waits = [];
  for (const agent of agents) {
    if (!scope || selected.has(agent.id)) {
      // An idle root may still receive another grant's completion notice.
      if (!scope || agent.status !== "idle") agent.cancel({ kind: "user" });
      waits.push(agent.whenIdle());
    }
    for (const job of ctx.jobs.list(agent.id)) {
      const owns =
        !scope ||
        (selected.has(agent.id) && agent.id !== scope.root) ||
        scope.jobs.some((j) => j.id === job.id && j.session_id === agent.id);
      if (owns && ACTIVE_JOB_STATES.has(job.status)) {
        ctx.jobs.kill(
          job.id,
          agent.id,
          scope ? "Task stopped" : "User cancelled execution",
        );
        jobs.push(ctx.jobs.wait(job.id, 5000, agent.id));
      }
    }
  }
  await Promise.all([...waits, ...jobs]);
  return { status: 200, body: "cancelled" };
}
