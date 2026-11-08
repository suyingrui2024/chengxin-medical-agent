// First-party Cordis adapter: cancel activity without discarding SDK sessions.
// Requests arrive on the existing SDK pipe; no additional listener is opened.
import { randomUUID } from "node:crypto";
import { observeTool } from "./execution-observations.mjs";
import { mergeCheckRequirement } from "./execution-delivery.mjs";
import { registerControl } from "./execution-control-channel.mjs";
import { createRootAdmission } from "./execution-admission.mjs";
import { registerBudgetContext } from "./execution-budget-context.mjs";
import { cancelRuntime } from "./execution-cancellation.mjs";
export const name = "chengxin-execution-control";
export const inject = ["agents", "jobs", "tools"];
export async function apply(ctx) {
  const admission = createRootAdmission(ctx);
  const epoch = randomUUID(),
    calls = new Map(),
    observations = [];
  let observationSequence = 0;
  const deliveryChecks = new Map();
  const incompleteDeliveryTurns = new Set();
  ctx.on("session/event", (session, event) => {
    if (!["tool/call", "tool/result"].includes(event.type)) return;
    const data = event.data || {};
    const id = data.callId || data.message?.toolCallId;
    const key = `${session.id}:${id}`;
    if (
      event.type === "tool/call" &&
      ["bash", "job_output", "delivery_check"].includes(data.name)
    ) {
      calls.set(key, {
        turn: data.turn,
        step: data.step,
        call_sequence: event.seq,
      });
      if (calls.size > 2048) calls.delete(calls.keys().next().value);
    } else if (event.type === "tool/result") calls.delete(key);
  });
  ctx.on("tools/result", (exec, result) => {
    const call = calls.get(`${exec.agent?.id}:${exec.callId}`);
    let value = observeTool(exec, result, call, epoch, observationSequence + 1);
    if (!value) return;
    if (
      value.tool === "delivery_check" &&
      value.check_key &&
      value.check_turn_id
    )
      value = mergeCheckRequirement(
        deliveryChecks.get(`${value.check_turn_id}:${value.check_key}`),
        value,
      );
    observationSequence++;
    observations.push(value);
    if (observations.length > 256) observations.shift();
    if (
      value.tool === "delivery_check" &&
      value.check_key &&
      value.check_turn_id
    ) {
      const key = `${value.check_turn_id}:${value.check_key}`;
      deliveryChecks.delete(key);
      deliveryChecks.set(key, value);
      if (deliveryChecks.size > 256) {
        const oldest = deliveryChecks.keys().next().value;
        incompleteDeliveryTurns.add(deliveryChecks.get(oldest).check_turn_id);
        deliveryChecks.delete(oldest);
        if (incompleteDeliveryTurns.size > 256)
          incompleteDeliveryTurns.delete(
            incompleteDeliveryTurns.values().next().value,
          );
      }
    }
  });
  registerBudgetContext(ctx);
  const handle = async (method, path, control = {}) => {
    const { scope, admission: requestAdmission } = control;
    const url = new URL(path, "http://runtime.local");
    if (method === "GET" && url.pathname === "/status") {
      const requested = Number(url.searchParams.get("after") || 0);
      if (!Number.isSafeInteger(requested) || requested < 0) {
        return { status: 400, body: "" };
      }
      const after = url.searchParams.get("epoch") === epoch ? requested : 0;
      const jobs = new Map();
      for (const agent of ctx.agents.list())
        for (const job of ctx.jobs.list(agent.id))
          jobs.set(job.id, {
            id: job.id,
            session_id: agent.id,
            status: job.status,
            kind: job.kind,
            detail: job.detail,
          });
      return {
        status: 200,
        body: JSON.stringify({
          jobs: [...jobs.values()],
          delivery_checks: [...deliveryChecks.values()],
          incomplete_delivery_turns: [...incompleteDeliveryTurns],
          observations: {
            epoch,
            first_sequence:
              observations[0]?.sequence || observationSequence + 1,
            last_sequence: observationSequence,
            items: observations
              .filter((item) => item.sequence > after)
              .slice(0, 64),
          },
        }),
      };
    }
    if (method === "GET" && path === "/health") {
      return { status: 200, body: "ready" };
    }
    if (method === "POST" && path === "/admission") {
      try {
        admission.register(requestAdmission?.task_id);
        return { status: 200, body: "admitted" };
      } catch {
        return { status: 400, body: "Invalid prompt admission" };
      }
    }
    if (method === "POST" && path === "/cancel")
      admission.revoke(scope, ctx.agents.list());
    if (method !== "POST" || path !== "/cancel") {
      return { status: 404, body: "" };
    }
    try {
      return await cancelRuntime(ctx, scope);
    } catch {
      return { status: 500, body: "Cancellation failed" };
    }
  };
  const unregister = registerControl(ctx.root, handle);
  ctx.effect(() => unregister);
}
