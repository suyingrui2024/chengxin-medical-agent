// Keep the official search provider and its response/cancellation semantics.
// Its private HTTP client has no session header, so use a scoped loopback route.
import { DeepSeekSearchProvider } from "@deepseek-ai/dsh-web-search-deepseek";
export const name = "chengxin-execution-search";
export const inject = ["web", "agents"];
export function apply(ctx) {
  ctx.web.registerSearchProvider(
    new DeepSeekSearchProvider(() => {
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
    }),
  );
}
