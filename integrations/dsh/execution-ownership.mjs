// Task grants outlive the foreground reply. Only canonical SDK parent/child
// notifications bind descendant sessions; an unknown session never adopts a grant.
export class ExecutionOwnership {
  constructor(current, root) {
    this.current = current;
    this.root = root;
    this.turns = new Map();
    this.sessions = new Map();
    this.waiters = new Map();
    this.canonicalTurns = new Map();
    this.parentOwners = new Map();
  }
  remember(owner) {
    this.turns.set(owner.id, owner);
    if (this.turns.size > 256)
      this.turns.delete(this.turns.keys().next().value);
  }
  forTurn(id) {
    return this.turns.get(id);
  }
  forSession(id) {
    if (!id || id === this.root()) return this.current();
    return this.turns.get(this.sessions.get(id));
  }
  recordTurn(session, turn, owner) {
    if (!owner || !Number.isSafeInteger(turn)) return;
    this.canonicalTurns.set(JSON.stringify([session, turn]), owner.id);
    if (this.canonicalTurns.size > 4096)
      this.canonicalTurns.delete(this.canonicalTurns.keys().next().value);
  }
  eventOwner(session, event) {
    const key = JSON.stringify([session, event?.data?.turn]);
    const owner = this.canonicalTurns.has(key)
      ? this.forTurn(this.canonicalTurns.get(key))
      : this.forSession(session);
    if (owner) {
      this.recordTurn(session, event?.data?.turn, owner);
      this.parentOwners.set(session, owner.id);
      if (this.parentOwners.size > 4096)
        this.parentOwners.delete(this.parentOwners.keys().next().value);
    }
    return owner;
  }
  bind(parent, child) {
    if (typeof child !== "string" || !child || child.length > 256) return;
    const owner = this.parentOwners.has(parent)
      ? this.forTurn(this.parentOwners.get(parent))
      : this.forSession(parent);
    if (!owner || this.sessions.has(child)) return;
    this.sessions.set(child, owner.id);
    if (this.sessions.size > 4096)
      this.sessions.delete(this.sessions.keys().next().value);
    for (const resolve of this.waiters.get(child) || []) resolve();
  }
  async forRequest(headers) {
    const sdk = headers["x-deepseek-harness-session-id"];
    const tool = headers["x-chengxin-session-id"];
    if (sdk && tool && sdk !== tool) return undefined;
    const id = sdk || tool;
    if (id && (typeof id !== "string" || id.length > 256)) return undefined;
    // The SDK pipe can deliver creation just after the first HTTP request.
    if (id && id !== this.root() && !this.sessions.has(id)) {
      if (this.waiters.size >= 64) return undefined;
      await new Promise((resolve) => {
        const waiting = this.waiters.get(id) || new Set();
        if (waiting.size >= 64) return resolve();
        const finish = () => {
          clearTimeout(timer);
          waiting.delete(finish);
          if (!waiting.size) this.waiters.delete(id);
          resolve();
        };
        const timer = setTimeout(finish, 1000);
        waiting.add(finish);
        this.waiters.set(id, waiting);
      });
    }
    return this.forSession(id);
  }
  cancelAll() {
    for (const owner of this.turns.values()) owner.cancelled = true;
  }
}

export function sessionHeaders(id) {
  return typeof id === "string" && id ? { "x-chengxin-session-id": id } : {};
}

export function scopedModelRequest(url) {
  const match = /^\/model\/sessions\/([^/?]+)\/(messages|v1\/messages)$/.exec(
    url,
  );
  if (!match) return null;
  try {
    const sessionId = decodeURIComponent(match[1]);
    if (
      !sessionId ||
      sessionId.length > 256 ||
      /[\x00-\x20\x7f/?#]/.test(sessionId)
    )
      return { invalid: true };
    return { sessionId, path: `/model/${match[2]}` };
  } catch {
    return { invalid: true };
  }
}
