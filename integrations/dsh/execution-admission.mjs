// A DSH root is shared by foreground prompts and automatic child/job notices.
// Keep their logical task identities at the inbox boundary, before a root turn
// can make another model request.
import { sessionHeaders } from "./execution-ownership.mjs";

const taskId = (value) =>
  typeof value === "string" && /^[0-9a-f-]{36}$/.test(value);

function noticeReference(message) {
  const source = message.source || {};
  if (
    ["agent-message", "subagent-settled"].includes(source.kind) &&
    typeof source.senderSessionId === "string" &&
    source.senderSessionId.length > 0 &&
    source.senderSessionId.length <= 256
  )
    return { sender_session_id: source.senderSessionId };
  return {};
}

export class RootAdmission {
  constructor({ resolveOwner, admit }) {
    this.resolveOwner = resolveOwner;
    this.admit = admit;
    this.pending = null;
    this.root = null;
    this.references = new Map();
  }

  register(id) {
    if (!taskId(id)) throw new Error("Invalid task admission");
    if (this.pending) throw new Error("A root prompt is already awaiting admission");
    this.pending = id;
  }

  inserted(agent, message) {
    const source = message.source || {};
    if (source.kind === "user" && this.pending && (!this.root || agent.id === this.root)) {
      this.root ??= agent.id;
      this.remember(message, { task_id: this.pending });
      this.pending = null;
      return;
    }
    if (agent.id !== this.root) return;
    this.remember(message, noticeReference(message));
  }

  discarded(message) {
    this.references.delete(message.id);
  }

  async decide(payload, next) {
    if (payload.agent.id !== this.root) return next();
    // Later steps of an already admitted turn often contain no new inbox
    // message (for example after a tool result). They retain that turn's
    // gateway binding and must not be treated as a fresh wakeup.
    if (payload.messages.length === 0) return next();
    const claimed = new Set(payload.messages.map((message) => message.id));
    const entries = await Promise.all(
      payload.messages.map(async (message) => ({
        message,
        reference: this.references.get(message.id) || noticeReference(message),
        owner: await this.ownerOf(message),
      })),
    );
    const direct = entries.find(
      ({ message, owner }) => message.source?.kind === "user" && taskId(owner),
    );
    const owner = direct?.owner || entries.find(({ owner: value }) => taskId(value))?.owner;
    if (!taskId(owner)) return { kind: "reject" };

    const keep = new Set(
      entries
        .filter(({ owner: value }) => value === owner)
        .map(({ message }) => message.id),
    );
    // A root turn claims all next-step messages plus one next-turn prompt. Do
    // not let an earlier completion notice accompany a newer user prompt.
    for (const { message, owner: value } of entries)
      if (value !== owner) payload.agent.followup(message);

    const accepted = await this.admit({
      session_id: payload.agent.id,
      sdk_turn: payload.turn,
      task_id: owner,
      signal: payload.signal,
    });
    if (!accepted) return { kind: "reject" };

    const decision = await next();
    if (decision.kind === "reject") return decision;
    return {
      ...decision,
      messages: decision.messages.filter(
        (message) => !claimed.has(message.id) || keep.has(message.id),
      ),
    };
  }

  remember(message, reference) {
    this.references.set(message.id, reference);
    if (this.references.size > 4096)
      this.references.delete(this.references.keys().next().value);
  }

  async ownerOf(message) {
    const reference = this.references.get(message.id) || noticeReference(message);
    if (taskId(reference.task_id)) return reference.task_id;
    if (!reference.sender_session_id) return null;
    const owner = await this.resolveOwner(reference.sender_session_id);
    if (taskId(owner)) {
      this.remember(message, { task_id: owner });
      return owner;
    }
    return null;
  }
}

async function json(response) {
  if (!response.ok) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export function createRootAdmission(ctx) {
  const base = process.env.DEEPSEEK_BASE_URL;
  const headers = (sessionId) => ({
    "x-api-key": process.env.DEEPSEEK_API_KEY,
    ...sessionHeaders(sessionId),
  });
  const admission = new RootAdmission({
    resolveOwner: async (sessionId) => {
      const response = await fetch(
        base + "/admission/owner?session_id=" + encodeURIComponent(sessionId),
        { headers: headers(admission.root), signal: AbortSignal.timeout(2000), redirect: "error" },
      );
      return (await json(response))?.task_id || null;
    },
    admit: async ({ session_id, sdk_turn, task_id, signal }) => {
      const response = await fetch(base + "/admission", {
        method: "POST",
        headers: { ...headers(session_id), "Content-Type": "application/json" },
        body: JSON.stringify({ task_id, turn: sdk_turn }),
        signal: AbortSignal.any([AbortSignal.timeout(2000), signal]),
        redirect: "error",
      });
      return (await json(response))?.ok === true;
    },
  });
  ctx.on("agent/inbox/inserted", ({ agent, message }) =>
    admission.inserted(agent, message),
  );
  ctx.on("agent/inbox/discarded", ({ message }) => admission.discarded(message));
  ctx.on(
    "agent/pre-step",
    (payload, next) => admission.decide(payload, next),
    { prepend: true },
  );
  return admission;
}
