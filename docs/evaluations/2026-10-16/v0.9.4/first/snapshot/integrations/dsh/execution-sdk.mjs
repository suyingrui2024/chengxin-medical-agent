// First-party persistence adapter for the pinned official MIT SDK server.
// Keep official protocol validation, event routing and lifecycle ownership.
import { HarnessSdkJsonRpcServer } from "@deepseek-ai/dsh-sdk-jsonrpc-server";
import { JsonRpcLineTransport } from "@deepseek-ai/dsh-sdk-protocol";
import { brandString } from "@deepseek-ai/dsh-brand";

export const name = "chengxin-execution-sdk";
export const inject = ["agents", "sessionPersistence"];

class PersistentSdkServer extends HarnessSdkJsonRpcServer {
  async createSession(sessionId) {
    const identity = brandString(sessionId);
    const saved = await this.ctx.sessionPersistence.stat(identity);
    if (!saved) {
      if (sessionId === process.env.CHENGXIN_RESUME_SESSION_ID)
        throw new Error(
          "Persisted execution history is missing; refusing a blank replacement session.",
        );
      return super.createSession(sessionId);
    }
    if (
      saved.header.cwd !== this.cwd ||
      saved.header.parentSession ||
      saved.header.origin === "subagent"
    )
      throw new Error(
        "Persisted execution session ownership does not match this workspace.",
      );
    const record = {
      handle: await this.ctx.agents.resume({
        resumeSessionId: identity,
        agentOptions: {
          provider: this.provider,
          model: this.model,
          ...(this.reasoningEffort === undefined
            ? {}
            : { reasoningEffort: this.reasoningEffort }),
          ...(this.maxTokens === undefined
            ? {}
            : { maxTokens: this.maxTokens }),
        },
      }),
    };
    this.sessions.set(sessionId, record);
    return record;
  }
}

export function apply(ctx) {
  const transport = new JsonRpcLineTransport(process.stdin, process.stdout);
  const server = new PersistentSdkServer(ctx, transport, {
    maxTokensAsSuccess: false,
  });
  let exiting;
  const shutdown = () =>
    (exiting ??= (async () => {
      await Promise.allSettled([transport.flush()]);
      await Promise.allSettled([ctx.root.fiber.dispose()]);
      process.exit(0);
    })());
  transport.onRequest(async (method, params) => {
    if (method === "initialize") await ctx.get("loader")?.await();
    const result = await server.handleRequest(method, params);
    if (method === "shutdown") setImmediate(() => shutdown());
    return result;
  });
  ctx.effect(() => {
    transport.start();
    return async () => {
      await server.shutdown();
      transport.close();
    };
  }, "chengxin-jsonrpc.serve");
}
