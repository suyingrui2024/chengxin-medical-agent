class Subscription {
  constructor() {
    this.queue = [];
    this.waiters = [];
    this.closed = false;
  }
  push(value) {
    if (this.closed) return;
    const waiter = this.waiters.shift();
    if (waiter) waiter(value);
    else this.queue.push(value);
  }
  next() {
    if (this.queue.length) return Promise.resolve(this.queue.shift());
    if (this.closed) return Promise.reject(new Error("subscription closed"));
    return new Promise((resolve) => this.waiters.push(resolve));
  }
  close() {
    this.closed = true;
    for (const resolve of this.waiters)
      resolve(Promise.reject(new Error("subscription closed")));
    this.waiters = [];
  }
  async *[Symbol.asyncIterator]() {
    for (;;) yield await this.next();
  }
}

export class DeepSeekHarness {
  constructor(options) {
    this.options = options;
    this.subscriptions = new Set();
    this.turn = 0;
    this.message = 0;
    this.pendingTask = null;
    this.client = {
      request: async (method, parameters = {}) => {
        if (method === "chengxin/control" && parameters.path === "/admission")
          this.pendingTask = parameters.admission?.task_id || null;
        return {
          status: 200,
          body:
            parameters.path === "/health"
              ? "ready"
              : JSON.stringify({
                  jobs: [],
                  delivery_checks: [],
                  incomplete_delivery_turns: [],
                  observations: {
                    epoch: "fixture",
                    first_sequence: 1,
                    last_sequence: 0,
                    items: [],
                  },
                }),
        };
      },
      prompt: async (sessionId) => {
        const messageId = `fixture-message-${++this.message}`;
        void this.respond(sessionId, this.pendingTask);
        return messageId;
      },
      subscribeSessionTree: () => {
        const subscription = new Subscription();
        this.subscriptions.add(subscription);
        const close = subscription.close.bind(subscription);
        subscription.close = () => {
          this.subscriptions.delete(subscription);
          close();
        };
        return subscription;
      },
    };
  }
  async start() {}
  async close() {
    for (const subscription of this.subscriptions) subscription.close();
  }
  publish(notification) {
    for (const subscription of this.subscriptions)
      subscription.push(notification);
  }
  async respond(sessionId, taskId) {
    const turn = ++this.turn;
    const headers = {
      "x-api-key": this.options.env.DEEPSEEK_API_KEY,
      "x-chengxin-session-id": sessionId,
      "content-type": "application/json",
    };
    const admitted = await fetch(
      this.options.env.DEEPSEEK_BASE_URL + "/admission",
      {
        method: "POST",
        headers,
        body: JSON.stringify({ task_id: taskId, turn }),
      },
    );
    if (!admitted.ok) return;
    const event = (type, data) =>
      this.publish({
        method: "session.event",
        params: { sessionId, event: { type, data } },
      });
    this.publish({
      method: "session.status",
      params: { sessionId, status: "running" },
    });
    event("turn/start", { turn });
    const response = await fetch(
      this.options.env.DEEPSEEK_BASE_URL + "/messages",
      {
        method: "POST",
        headers: { ...headers, "x-deepseek-harness-session-id": sessionId },
        body: JSON.stringify({
          model: "fixture",
          max_tokens: 64,
          messages: [],
        }),
      },
    );
    if (!response.ok) return;
    const text = await response.text();
    event("assistant/message", {
      turn,
      step: 1,
      message: { role: "assistant", content: [{ type: "text", text }] },
    });
    event("turn/end", { turn, reason: { kind: "completed" } });
    this.publish({
      method: "session.status",
      params: { sessionId, status: "idle" },
    });
  }
}
