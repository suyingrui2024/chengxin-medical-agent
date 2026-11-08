// Bind SDK job notices to the canonical tool call that created the job.
// The pinned SDK notice carries its job ID in a fixed envelope, not in source.
// Never derive ownership from the command's stdout or from a current grant.
function remember(map, id, value) {
  map.set(id, value);
  if (map.size > 4096) map.delete(map.keys().next().value);
}

export class JobAdmission {
  #sessions = new WeakMap();

  admitted(session, turn, owner) {
    if (!session) return;
    let state = this.#sessions.get(session);
    if (!state) {
      state = { turns: new Map(), calls: new Map(), jobs: new Map() };
      this.#sessions.set(session, state);
    }
    remember(state.turns, turn, owner);
  }

  observe(session, event) {
    const state = this.#sessions.get(session);
    if (!state) return;
    const data = event.data || {};
    if (event.type === "tool/call") {
      const owner = state.turns.get(data.turn);
      if (owner) remember(state.calls, data.callId, owner);
    } else if (event.type === "job/registered" && data.owner === session.id) {
      const owner = state.calls.get(data.toolCall?.rootCallId);
      if (owner && !state.jobs.has(data.jobId))
        remember(state.jobs, data.jobId, owner);
    }
  }

  reference(session, message) {
    if (
      message.source?.kind !== "tool-jobs" ||
      message.source.form !== "notice"
    )
      return {};
    const text = message.content?.[0];
    if (text?.type !== "text") return {};
    const id = /^background job ([a-z][\w-]{0,127})(?=[\s(]|$)/.exec(
      text.text,
    )?.[1];
    const owner = id && this.#sessions.get(session)?.jobs.get(id);
    return owner ? { task_id: owner } : {};
  }
}
