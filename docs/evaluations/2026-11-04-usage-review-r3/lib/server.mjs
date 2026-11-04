/**
 * 起停 demo 服务：本进程内 spawn → 等健康 → 跑完 kill。
 *
 * 为什么不用 nohup/&：沙箱会在两次调用之间回收后台进程，走查跑到一半
 * 服务就没了；而"复用别人已经在跑的服务"正是上一轮读错会话的根因。
 * 所以这里先探测端口，被占用就直接报错，绝不静默连上去。
 */
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** 仓库根：lib → 走查目录 → evaluations → docs → 根。 */
export const ROOT = path.resolve(HERE, "../../../..");

const PY = path.join(ROOT, ".venv/bin/python");

/** 端口上是否已有服务在听。 */
function probe(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    const done = (busy) => {
      socket.destroy();
      resolve(busy);
    };
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
    socket.setTimeout(800, () => done(false));
  });
}

async function waitForHealth(base, timeoutMs = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(`${base}/api/health`);
      if (response.ok) {
        const body = await response.json();
        if (body.status === "ok" || body.status === undefined) return body;
      }
    } catch {
      /* 还没起来 */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`服务在 ${timeoutMs}ms 内没有通过健康检查`);
}

/**
 * 起一个 demo 服务。返回 { base, stop, output }。
 * 调用方必须 try/finally 里 stop()，否则进程会熬到沙箱回收。
 */
export async function startServer({ port = 19021, dataDir } = {}) {
  if (await probe(port)) {
    throw new Error(
      `端口 ${port} 已有服务在听——拒绝复用它（上一轮就是这样读到别人的会话）。换端口或先关掉。`,
    );
  }

  const child = spawn(
    PY,
    ["-m", "medagent.server", "--backend", "demo", "--port", String(port)],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        PYTHONPATH: "src",
        MEDAGENT_PATIENT_DATA_DIR: dataDir,
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  let output = "";
  child.stdout.on("data", (chunk) => {
    output += chunk.toString();
  });
  child.stderr.on("data", (chunk) => {
    output += chunk.toString();
  });

  const base = `http://127.0.0.1:${port}`;
  try {
    await waitForHealth(base);
  } catch (error) {
    child.kill("SIGKILL");
    throw new Error(
      `${error.message}\n--- 服务输出 ---\n${output.slice(-2000)}`,
    );
  }

  let stopped = false;
  const stop = async () => {
    if (stopped) return;
    stopped = true;
    child.kill("SIGTERM");
    for (let i = 0; i < 25; i += 1) {
      if (child.exitCode !== null) return;
      await new Promise((r) => setTimeout(r, 100));
    }
    child.kill("SIGKILL");
  };

  return { base, stop, output: () => output };
}
