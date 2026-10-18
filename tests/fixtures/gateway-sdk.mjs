export class DeepSeekHarness {
  constructor(options) {
    this.options = options;
    this.client = {
      request: async (_method, { path }) => ({
        status: 200,
        body:
          path === "/health"
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
      }),
      subscribeSessionTree: () => ({
        close() {},
        [Symbol.asyncIterator]() {
          return { next: () => new Promise(() => {}) };
        },
      }),
    };
  }
  async start() {}
  async close() {}
  async run(_text, { sessionId }) {
    const response = await fetch(
      this.options.env.DEEPSEEK_BASE_URL + "/messages",
      {
        method: "POST",
        headers: {
          "x-api-key": this.options.env.DEEPSEEK_API_KEY,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          model: "fixture",
          max_tokens: 64,
          messages: [],
        }),
      },
    );
    if (!response.ok) throw new Error("Gateway fixture request failed");
    return {
      events: [{ type: "turn/end", data: { reason: { kind: "completed" } } }],
      finalResponse: await response.text(),
    };
  }
}
