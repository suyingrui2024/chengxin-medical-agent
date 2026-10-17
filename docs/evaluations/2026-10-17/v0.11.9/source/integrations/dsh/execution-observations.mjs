// Research UI observations from canonical SDK values, never stdout interpretation.
import { validCheckpoint } from "./execution-checkpoints.mjs";
export const ACTIVE_JOB_STATES = new Set(["starting", "running", "stopping"]);
export const hasActiveJobs = (jobs) =>
  jobs.some((job) => ACTIVE_JOB_STATES.has(job.status));
export const observationKey = (session, turn, step, call) =>
  `${session}:${turn}:${step}:${call}`;
const bounded = (value, maximum = 256) =>
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= maximum &&
  !/[\x00-\x1f]/.test(value)
    ? value
    : null;
const integer = (value) => Number.isSafeInteger(value) && value >= 0;

export function observeTool(exec, result, call, epoch, sequence) {
  if (
    !call ||
    exec.parent !== undefined ||
    !["bash", "job_output", "delivery_check"].includes(exec.name)
  )
    return null;
  const session = bounded(exec.agent?.id),
    identity = bounded(exec.callId);
  if (
    !session ||
    !identity ||
    !integer(call.turn) ||
    !integer(call.step) ||
    !integer(call.call_sequence)
  )
    return null;
  const observation = {
    schema: "execution-observation/v1",
    epoch,
    sequence,
    session_id: session,
    call_id: identity,
    sdk_turn: call.turn,
    sdk_step: call.step,
    call_sequence: call.call_sequence,
    tool: exec.name,
    state: "unavailable",
    exit_code: null,
    signal: null,
    job_id: null,
    job_kind: null,
    wait_ms: null,
  };
  if (result.isError) return { ...observation, state: "failed" };
  const value = result.value;
  if (!value || typeof value !== "object") return observation;
  if (exec.name === "delivery_check") {
    if (value.schema !== "execution-delivery/v1") return observation;
    if (
      typeof value.check_key !== "string" ||
      !/^[0-9a-f]{64}$/.test(value.check_key) ||
      !bounded(value.label, 80) ||
      typeof value.owner_turn_id !== "string" ||
      !/^[0-9a-f-]{36}$/.test(value.owner_turn_id)
    )
      return { ...observation, state: "failed" };
    observation.check_key = value.check_key;
    observation.check_label = value.label;
    observation.check_turn_id = value.owner_turn_id;
    observation.check_required = value.required !== false;
    if (Object.hasOwn(value, "checkpoint"))
      observation.check_snapshot = validCheckpoint(value.checkpoint)
        ? value.checkpoint
        : null;
    observation.exit_code = Number.isSafeInteger(value.exit_code)
      ? value.exit_code
      : null;
    observation.state = [
      "completed",
      "failed",
      "unavailable",
      "timed_out",
      "cancelled",
    ].includes(value.state)
      ? value.state
      : "unavailable";
    observation.job_id = bounded(value.job_id);
    observation.job_kind = "bash";
  } else if (exec.name === "bash" && value.kind === "foreground") {
    observation.exit_code = Number.isSafeInteger(value.exitCode)
      ? value.exitCode
      : null;
    observation.signal = bounded(value.signal, 64);
    observation.state =
      value.aborted || value.stopped
        ? "cancelled"
        : value.timedOut
          ? "timed_out"
          : observation.signal
            ? "killed"
            : observation.exit_code !== null
              ? "completed"
              : "unavailable";
  } else if (
    exec.name === "bash" &&
    ["background", "promoted"].includes(value.kind)
  ) {
    observation.job_id = bounded(value.jobId);
    observation.job_kind = "bash";
    observation.state = observation.job_id ? value.kind : "unavailable";
    observation.wait_ms =
      Number.isFinite(value.timeoutMs) && value.timeoutMs >= 0
        ? value.timeoutMs
        : null;
  } else if (exec.name === "job_output" && value.job) {
    const job = value.job;
    observation.job_id = bounded(job.id);
    observation.job_kind = bounded(job.kind, 64);
    const states = [
      "starting",
      "running",
      "stopping",
      "completed",
      "failed",
      "killed",
      "cancelled",
    ];
    observation.state =
      observation.job_id && states.includes(job.status)
        ? job.status
        : "unavailable";
    // This field is the Bash producer's process outcome, separate from its text.
    if (
      job.kind === "bash" &&
      job.status === "completed" &&
      typeof job.detail === "string"
    ) {
      const exit = /^exit code: (-?\d+)(?:;|$)/.exec(job.detail);
      if (exit && Number.isSafeInteger(Number(exit[1])))
        observation.exit_code = Number(exit[1]);
    }
    if (
      job.kind === "bash" &&
      job.status === "killed" &&
      typeof job.detail === "string"
    ) {
      const signal = /^signal: ([A-Z0-9]+)(?:;|$)/.exec(job.detail);
      if (signal) observation.signal = bounded(signal[1], 64);
    }
  }
  return observation;
}
