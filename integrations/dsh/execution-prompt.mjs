// The public SDK run() collects until the whole agent becomes idle. Independent
// logical tasks instead settle against the canonical turn admitting their ID.
export async function runOwnedPrompt(client, sessionId, text, options) {
  const subscription = client.subscribeSessionTree(sessionId);
  const events = [];
  const notifications = [];
  let messageId;
  let rootTurn;
  let aborted;
  const stop = new Promise((resolve) => { aborted = resolve; });
  const onAbort = () => aborted(null);
  options.signal.addEventListener("abort", onAbort, { once: true });
  try {
    if (options.signal.aborted) return result();
    await options.register();
    messageId = await client.prompt(sessionId, [{ type: "text", text }]);
    while (!options.signal.aborted) {
      const notification = await Promise.race([subscription.next(), stop]);
      if (!notification) break;
      if (notification.method !== "session.event" ||
          notification.params.sessionId !== sessionId) continue;
      const event = notification.params.event;
      if (!event || typeof event.type !== "string")
        throw new Error("Invalid canonical SDK event");
      if (event.type === "turn/start") rootTurn = event.data?.turn;
      const turn = event.data?.turn ?? rootTurn;
      if (options.promptForTurn(turn) !== messageId) continue;
      events.push(event);
      notifications.push(notification);
      if (event.type === "turn/end") return result();
    }
    if (!options.signal.aborted) throw new Error("SDK ended before the task turn");
    return result();
  } finally {
    options.signal.removeEventListener("abort", onAbort);
    subscription.close();
  }
  function result() {
    const message = events.filter((event) => event.type === "assistant/message")
      .at(-1)?.data?.message;
    if (message && (!Array.isArray(message.content) ||
        message.content.some((part) => typeof part?.type !== "string" ||
          (part.type === "text" && typeof part.text !== "string"))))
      throw new Error("Invalid SDK assistant content");
    return { sessionId, events, notifications,
      finalResponse: (message?.content || []).filter((part) => part.type === "text")
        .map((part) => part.text).join("\n") };
  }
}
