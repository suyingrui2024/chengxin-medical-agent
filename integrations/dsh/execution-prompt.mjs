function finalResponse(events) {
  const message = events
    .filter((event) => event.type === "assistant/message")
    .at(-1)?.data?.message;
  if (
    message &&
    (!Array.isArray(message.content) ||
      message.content.some(
        (part) =>
          typeof part?.type !== "string" ||
          (part.type === "text" && typeof part.text !== "string"),
      ))
  )
    throw new Error("Invalid SDK assistant content");
  return (message?.content || [])
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n");
}

// The SDK convenience run() waits for the whole shared root to become idle.
// This lower-level interval waits for the turn which the runtime admitted to
// this task id, so an older child completion cannot be collected as a newer
// foreground result.
export async function runOwnedPrompt({ client, sessionId, text, taskId, register, ownerForTurn, signal }) {
  const subscription = client.subscribeSessionTree(sessionId);
  const turns = new Map();
  let abort;
  const stopped = new Promise((resolve) => {
    abort = () => resolve(null);
    signal?.addEventListener("abort", abort, { once: true });
  });
  try {
    if (signal?.aborted) return { sessionId, events: [], notifications: [], finalResponse: "" };
    await register(taskId);
    await client.prompt(sessionId, [{ type: "text", text }]);
    while (true) {
      const notification = await Promise.race([subscription.next(), stopped]);
      if (notification === null)
        return { sessionId, events: [], notifications: [], finalResponse: "" };
      if (
        notification.method !== "session.event" ||
        notification.params.sessionId !== sessionId
      )
        continue;
      const event = notification.params.event;
      const turn = event?.data?.turn;
      if (!Number.isSafeInteger(turn)) continue;
      const events = turns.get(turn) || [];
      events.push(event);
      turns.set(turn, events);
      if (event.type !== "turn/end" || ownerForTurn(turn) !== taskId)
        continue;
      return {
        sessionId,
        events,
        notifications: [],
        finalResponse: finalResponse(events),
      };
    }
  } finally {
    signal?.removeEventListener("abort", abort);
    subscription.close();
  }
}
