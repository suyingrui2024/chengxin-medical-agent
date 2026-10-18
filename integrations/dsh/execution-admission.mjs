// Logical tasks share one SDK root. Admit its canonical inbox messages before
// prompt assembly, and defer another task's steering until a separate turn.
export class RootAdmission {
  constructor(root, admit) {
    this.root = root;
    this.admit = admit;
    this.messages = new Map();
    this.turns = new Map();
    this.revoked = new Set();
    this.jobEvents = [];
    this.jobNotices = [];
    this.killedJobs = new Set();
    this.current = null;
    this.pending = null;
    this.stopped = false;
  }
  register(task) {
    if (typeof task !== "string" || !/^[0-9a-f-]{36}$/.test(task))
      throw new Error("Invalid task registration");
    if (this.pending) throw new Error("A prompt is already registered");
    this.pending = task;
    this.stopped = false;
  }
  remember(message, reference) {
    this.messages.set(message.id, reference);
    if (this.messages.size > 4096)
      this.messages.delete(this.messages.keys().next().value);
  }
  inserted(agent, message) {
    if (agent.id !== this.root || this.messages.has(message.id)) return;
    const source = message.source || {};
    if (source.kind === "user") {
      this.remember(message, this.pending ? { task_id: this.pending } : {});
      this.pending = null;
    } else if (
      ["subagent-settled", "agent-message"].includes(source.kind) &&
      typeof source.senderSessionId === "string"
    ) {
      this.remember(message, { sender_session_id: source.senderSessionId });
    } else if (source.kind === "tool-jobs") {
      this.jobNotices.push(message);
      this.pairJobs();
    } else this.remember(message, {});
  }
  settled(event) {
    if (
      event.type !== "settled" ||
      event.job.owner !== this.root ||
      this.killedJobs.delete(event.job.id) ||
      event.awaited ||
      event.cause === "teardown"
    )
      return;
    this.jobEvents.push(event.job.id);
    this.pairJobs();
  }
  pairJobs() {
    // The pinned job controller emits one canonical inbox notice synchronously
    // in this same public settled-event dispatch. Either subscriber may run
    // first. Never infer a job identity from generated notice text.
    while (this.jobEvents.length && this.jobNotices.length)
      this.remember(this.jobNotices.shift(), { job_id: this.jobEvents.shift() });
    if (this.jobPairCheck) return;
    this.jobPairCheck = true;
    queueMicrotask(() => {
      this.jobPairCheck = false;
      // A missing delivery cannot be matched to a later, unrelated settlement.
      for (const notice of this.jobNotices) this.remember(notice, {});
      this.jobNotices = [];
      this.jobEvents = [];
    });
  }
  start(turn) {
    const state = { turn, claimed: [], owner: null, active: true, waiters: [] };
    this.turns.set(turn, state);
    this.current = state;
    if (this.turns.size > 4096)
      this.turns.delete(this.turns.keys().next().value);
  }
  claimed(agent, message, turn) {
    if (agent.id !== this.root) return;
    this.turns.get(turn)?.claimed.push(message);
  }
  end(turn) {
    const state = this.turns.get(turn);
    if (!state) return;
    state.active = false;
    for (const resolve of state.waiters) resolve();
    state.waiters = [];
  }
  get currentTask() {
    return this.current?.active ? this.current.owner : null;
  }
  async prepare(agent) {
    if (agent?.id !== this.root) return;
    const state = this.current;
    if (!state?.active) throw new Error("Root admission has no open turn");
    if (state.prepared === state.claimed.length && state.result) return;
    if (state.preparing) return state.preparing;
    state.preparing = (async () => {
      await Promise.resolve(); // finish synchronous job-notice correlation
      const messages = state.claimed.map((message) => ({
        message_id: message.id,
        kind: message.source?.kind === "user" ? "user" : "notice",
        ...(this.messages.get(message.id) || {}),
      }));
      const result = await this.admit({
        session_id: this.root,
        sdk_turn: state.turn,
        ...(state.owner ? { pinned_task_id: state.owner } : {}),
        messages,
      });
      state.result = result;
      state.owner = result.turn_id;
      state.prepared = state.claimed.length;
      for (const value of result.messages)
        if (value.turn_id)
          this.messages.set(value.message_id, { task_id: value.turn_id });
    })();
    try {
      await state.preparing;
    } finally {
      state.preparing = null;
    }
  }
  async decide(payload, next) {
    if (payload.agent.id !== this.root) return next();
    await this.prepare(payload.agent);
    const state = this.current;
    const decision = await next();
    if (decision.kind === "reject") return decision;
    const keep = new Set();
    const claimed = new Set(state.claimed.map((message) => message.id));
    for (const value of state.result.messages) {
      if (!value.allowed || this.stopped || this.revoked.has(value.turn_id))
        continue;
      if (value.turn_id === state.owner) keep.add(value.message_id);
      else {
        const message = state.claimed.find((m) => m.id === value.message_id);
        if (message) payload.agent.followup(message);
      }
    }
    const foreignOnly = claimed.size > 0 && keep.size === 0;
    state.claimed = [];
    state.prepared = undefined;
    if (
      !state.result.allowed ||
      this.stopped ||
      this.revoked.has(state.owner)
    )
      return { kind: "reject" };
    return {
      ...decision,
      messages: decision.messages.filter((message) =>
        claimed.has(message.id) ? keep.has(message.id) : !foreignOnly,
      ),
    };
  }
  idle(agent) {
    if (agent.id !== this.root) return;
    queueMicrotask(() => {
      if (this.stopped || agent.status !== "idle") return;
      // A rejected SDK pre-step ends its driver without draining later input.
      // Requeue the last retained item through public Inbox/Agent operations;
      // its identity and FIFO order stay intact, and no fake prompt is added.
      const queued = agent.inbox.nextTurn;
      const message = queued.at(-1) || agent.inbox.nextStep.at(-1);
      if (!message) return;
      const owner = this.messages.get(message.id)?.task_id;
      if (owner && this.revoked.has(owner)) {
        agent.inbox.remove(message.id);
        return this.idle(agent);
      }
      agent.inbox.remove(message.id);
      if (queued.length) agent.followup(message);
      else agent.steer(message);
    });
  }
  revoke(scope, agent) {
    if (!scope) {
      this.stopped = true;
      this.pending = null;
      return;
    }
    this.revoked.add(scope.task_id);
    if (this.pending === scope.task_id) this.pending = null;
    for (const message of [...agent.inbox.nextTurn, ...agent.inbox.nextStep]) {
      const ref = this.messages.get(message.id) || {};
      if (
        ref.task_id === scope.task_id ||
        scope.sessions.includes(ref.sender_session_id) ||
        scope.jobs.some((job) => job.id === ref.job_id)
      )
        agent.inbox.remove(message.id);
    }
  }
  waitForEnd() {
    const state = this.current;
    if (!state?.active) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const done = () => {
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(() => {
        state.waiters = state.waiters.filter((value) => value !== done);
        reject(new Error("Root task did not stop"));
      }, 5000);
      state.waiters.push(done);
    });
  }
}
