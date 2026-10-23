// Give web_fetch the session attribution web_search already has. Fetch is
// plain HTTP with no inner model request to route through the scoped
// loopback gateway, so attribution is recorded as session events instead,
// and both web tools share the per-session budget (execution-web-budget).
// The upstream web-fetch-http plugin is disabled in execution.patch.yml;
// this plugin registers the delegating provider under the same "http" id,
// mirroring how execution-search replaces web-search-deepseek.
import {
  DEFAULT_USER_AGENT,
  HttpFetchProvider,
  LOCAL_FETCH_PROVIDER_ID,
} from "@deepseek-ai/dsh-web-fetch-http";
import { WebError } from "@deepseek-ai/dsh-web";
import { WEB_SESSION_CAP, claimWebCall } from "./execution-web-budget.mjs";

export const name = "chengxin-execution-fetch";
export const inject = ["web", "agents"];

// The limits the upstream plugin applies by default; keep fetch semantics
// identical to upstream and add attribution around them, nothing more.
const DEFAULT_LIMITS = {
  maxResponseBytes: 5e6,
  maxBodyChars: 1e5,
  timeoutMs: 30000,
  maxRedirects: 5,
  userAgent: DEFAULT_USER_AGENT,
};

export function createAttributedFetchProvider(inner, agents) {
  return {
    id: LOCAL_FETCH_PROVIDER_ID,
    available() {
      return inner.available();
    },
    async fetch(request, signal) {
      const agent = agents.currentInitiator();
      if (!agent) return inner.fetch(request, signal);
      const session = agent.session;
      if (!claimWebCall(session?.id)) {
        session?.append("web/budget-exceeded", {
          kind: "fetch",
          cap: WEB_SESSION_CAP,
        });
        throw new WebError(
          `web fetch refused: this session has reached the web budget of ${WEB_SESSION_CAP} calls`,
          "WEB_SESSION_BUDGET_EXCEEDED",
        );
      }
      session?.append("web/fetch-request", { url: request.url });
      try {
        const result = await inner.fetch(request, signal);
        session?.append("web/fetch-result", {
          url: request.url,
          statusCode: result.statusCode,
        });
        return result;
      } catch (error) {
        session?.append("web/fetch-result", {
          url: request.url,
          error: error?.message || String(error),
        });
        throw error;
      }
    },
  };
}

export function apply(ctx) {
  ctx.web.registerFetchProvider(
    createAttributedFetchProvider(
      new HttpFetchProvider(DEFAULT_LIMITS),
      ctx.agents,
    ),
  );
}
