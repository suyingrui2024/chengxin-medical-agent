// Research questions belong to a live host turn; no model-supplied answer exists.
import { randomUUID } from "node:crypto";

const text = (value, maximum) =>
  typeof value === "string" &&
  value.trim().length > 0 &&
  value.length <= maximum &&
  !/[\x00-\x08\x0b-\x1f]/.test(value);
const send = (response, status, value) => {
  if (response.destroyed || response.writableEnded) return;
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(value));
};

export class ClarificationBroker {
  constructor({ owner, emit, addSource }) {
    this.owner = owner;
    this.emit = emit;
    this.addSource = addSource;
    this.pending = new Map();
    this.lifetime = 0;
  }
  async request(request, response) {
    const owned = this.owner();
    const fail = (status, message) =>
      send(response, status, { ok: false, error: { message } });
    if (!owned || owned.cancelled || Date.now() >= owned.deadline)
      return fail(409, "当前任务已停止");
    if (
      this.pending.size >= 3 ||
      (owned.clarificationCalls || 0) >= 8 ||
      this.lifetime >= 64
    )
      return fail(429, "已达到本轮澄清次数或同时等待上限");
    let args;
    try {
      const chunks = [];
      let bytes = 0;
      for await (const chunk of request) {
        bytes += chunk.length;
        if (bytes > 12000) return fail(413, "澄清参数过长");
        chunks.push(chunk);
      }
      args = JSON.parse(Buffer.concat(chunks));
      if (
        !args ||
        Array.isArray(args) ||
        Object.keys(args).some(
          (key) =>
            !["question", "context", "options", "session_id"].includes(key),
        ) ||
        !text(args.question, 240) ||
        !text(args.session_id, 256) ||
        (args.context !== undefined && !text(args.context, 320)) ||
        (args.options !== undefined &&
          (!Array.isArray(args.options) ||
            ![0, 2, 3].includes(args.options.length) ||
            args.options.some(
              (option) => !text(option, 80) || /[\x00-\x1f]/.test(option),
            ) ||
            new Set(args.options.map((option) => option.trim())).size !==
              args.options.length))
      )
        return fail(422, "请提供简短问题及零个或两至三个互不重复的选项");
    } catch {
      return fail(400, "澄清参数无效");
    }
    // Recheck after body input: cancellation/other requests may have advanced.
    if (
      this.owner() !== owned ||
      owned.cancelled ||
      Date.now() >= owned.deadline
    )
      return fail(409, "当前任务已停止");
    if (
      this.pending.size >= 3 ||
      (owned.clarificationCalls || 0) >= 8 ||
      this.lifetime >= 64
    )
      return fail(429, "澄清次数已达上限");
    owned.clarificationCalls = (owned.clarificationCalls || 0) + 1;
    this.lifetime++;
    const value = {
      schema: "execution-clarification/v1",
      id: randomUUID(),
      turn_id: owned.id,
      session_id: args.session_id,
      question: args.question.trim(),
      context: args.context?.trim() || "",
      options: (args.options || []).map((option) => option.trim()),
      created_at: Date.now() / 1000,
      deadline_at: owned.deadline / 1000,
      status: "pending",
      answer: null,
      reason: null,
    };
    this.pending.set(value.id, { value, response, owned });
    response.once("close", () => {
      if (!this.pending.delete(value.id)) return;
      this.emit("clarification.closed", {
        turn_id: value.turn_id,
        clarification: {
          ...value,
          status: "closed",
          reason: "transport_closed",
        },
      });
    });
    this.emit("clarification.requested", {
      turn_id: owned.id,
      clarification: value,
    });
  }
  answer(payload) {
    const pending = this.pending.get(payload.request_id);
    if (
      !pending ||
      pending.value.turn_id !== payload.turn_id ||
      this.owner() !== pending.owned ||
      pending.owned.cancelled ||
      Date.now() >= pending.owned.deadline ||
      !text(payload.answer, 2000)
    ) {
      this.emit("clarification.answer_rejected", {
        turn_id: payload.turn_id,
        request_id: payload.request_id,
      });
      return;
    }
    this.pending.delete(payload.request_id);
    const value = {
      ...pending.value,
      status: "answered",
      answer: payload.answer.trim(),
    };
    this.addSource({ id: value.id, text: value.answer });
    this.emit("clarification.answered", {
      turn_id: value.turn_id,
      clarification: value,
    });
    send(pending.response, 200, {
      ok: true,
      result: {
        schema: "execution-answer/v1",
        request_id: value.id,
        source: "user",
        answer: value.answer,
      },
    });
  }
  cancel(turnId, reason) {
    for (const [id, pending] of this.pending) {
      if (turnId && pending.value.turn_id !== turnId) continue;
      this.pending.delete(id);
      const value = { ...pending.value, status: "closed", reason };
      this.emit("clarification.closed", {
        turn_id: value.turn_id,
        clarification: value,
      });
      send(pending.response, 409, {
        ok: false,
        error: { message: "此问题已随任务停止，请在后续任务中重新说明需求" },
      });
    }
  }
}
