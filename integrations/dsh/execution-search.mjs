// Keep the official search provider and its response/cancellation semantics.
// Its private HTTP client has no session header, so use a scoped loopback route.
// The registered provider delegates to it unchanged; the only addition is the
// shared per-session web budget (execution-web-budget), claimed before each
// search so search and fetch together stay under the session cap.
import { DeepSeekSearchProvider } from "@deepseek-ai/dsh-web-search-deepseek";
import { WebError } from "@deepseek-ai/dsh-web";
import { WEB_SESSION_CAP, claimWebCall } from "./execution-web-budget.mjs";
export const name = "chengxin-execution-search";
export const inject = ["web", "agents"];
export function apply(ctx) {
  const inner = new DeepSeekSearchProvider(() => {
    const agent = ctx.agents.currentInitiator();
    return {
      apiKey: process.env.DEEPSEEK_API_KEY,
      baseURL: `${process.env.DEEPSEEK_BASE_URL}/sessions/${encodeURIComponent(agent?.session.id || "unattributed")}`,
      model: process.env.CHENGXIN_MODEL,
      apiVersion: "2023-06-01",
      maxUses: 3,
      maxTokens: 2048,
      recordRequest: (request) =>
        agent?.session.append("web/deepseek-search-llm-request", request),
    };
  });
  ctx.web.registerSearchProvider({
    id: inner.id,
    available: () => inner.available(),
    async search(request, signal) {
      const agent = ctx.agents.currentInitiator();
      if (!claimWebCall(agent?.session.id)) {
        agent?.session.append("web/budget-exceeded", {
          kind: "search",
          cap: WEB_SESSION_CAP,
        });
        throw new WebError(
          `web search refused: this session has reached the web budget of ${WEB_SESSION_CAP} calls`,
          "WEB_SESSION_BUDGET_EXCEEDED",
        );
      }
      return inner.search(request, signal);
    },
  });
}
