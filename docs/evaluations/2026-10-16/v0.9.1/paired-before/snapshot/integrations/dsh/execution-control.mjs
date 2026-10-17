// First-party Cordis adapter: cancel activity without discarding SDK sessions.
// The socket is in this runtime's private state mount; it grants no host access.
import { createServer } from "node:http";
import { lstat, unlink } from "node:fs/promises";
export const name = "chengxin-execution-control";
export const inject = ["agents", "jobs", "systemPrompt"];
export async function apply(ctx) {
  ctx.on(
    "system-prompt/assemble",
    async (_assembly, context, next) => {
      const assembly = await next();
      try {
        const response = await fetch(
          process.env.DEEPSEEK_BASE_URL + "/budget",
          {
            headers: { "x-api-key": process.env.DEEPSEEK_API_KEY },
            signal: AbortSignal.timeout(2000),
            redirect: "error",
          },
        );
        if (!response.ok) return assembly;
        const budget = await response.json();
        assembly.contexts.push({
          name: "chengxin-execution-budget",
          text: `本轮全体智能体共享剩余 ${budget.remaining_calls} 次模型调用（包括即将进行的调用），剩余执行时间约 ${budget.remaining_seconds} 秒。优先完成当前用户要求和保存产物；复核子任务限定范围，避免父子重复检查。剩余不足 4 次时停止扩大检查范围，明确报告已完成项与未完成项并收尾。`,
        });
      } catch {
        /* The gateway still enforces limits when advisory context is unavailable. */
      }
      return assembly;
    },
    { prepend: true },
  );
  const socket = "/state/control.sock";
  try {
    const info = await lstat(socket);
    if (!info.isSocket()) throw new Error("Invalid control socket");
    await unlink(socket);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const server = createServer(async (request, response) => {
    if (request.method === "GET" && request.url === "/status") {
      const jobs = new Map();
      for (const agent of ctx.agents.list())
        for (const job of ctx.jobs.list(agent.id))
          jobs.set(job.id, {
            id: job.id,
            status: job.status,
            kind: job.kind,
            detail: job.detail,
          });
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ jobs: [...jobs.values()] }));
      return;
    }
    if (request.method === "GET" && request.url === "/health") {
      response.end("ready");
      return;
    }
    if (request.method !== "POST" || request.url !== "/cancel") {
      response.writeHead(404);
      response.end();
      return;
    }
    try {
      const agents = ctx.agents.list();
      const jobs = [];
      for (const agent of agents) {
        agent.cancel({ kind: "user" });
        for (const job of ctx.jobs.list(agent.id)) {
          if (["running", "starting", "stopping"].includes(job.status)) {
            ctx.jobs.kill(job.id, agent.id, "User cancelled execution");
            jobs.push(ctx.jobs.wait(job.id, 5000, agent.id));
          }
        }
      }
      await Promise.all([...agents.map((agent) => agent.whenIdle()), ...jobs]);
      response.end("cancelled");
    } catch {
      response.writeHead(500);
      response.end("Cancellation failed");
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(socket, resolve);
  });
  ctx.effect(() => () => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
}
