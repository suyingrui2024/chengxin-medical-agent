// Real child process, file bytes and Python checkpoint service; no model requests.
import { mkdtemp, writeFile, readFile, access, rm } from "node:fs/promises";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { apply } from "../../integrations/dsh/execution-delivery.mjs";

export async function actualInputCheck({ strict = true, mutate = false } = {}) {
  const root = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../..",
  );
  const run = promisify(execFile),
    directory = await mkdtemp(path.join(root, ".runtime/input-witness-"));
  const python =
    process.env.MEDAGENT_PYTHON ||
    (process.platform === "win32"
      ? path.join(root, ".runtime/windows/python/python.exe")
      : "python3");
  const checkpoint = async (paths) =>
    JSON.parse(
      (
        await run(python, [
          "-c",
          "import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from medagent.execution_checkpoints import checkpoint;p=Path(sys.argv[2]);s=p.stat();print(json.dumps(checkpoint(p,(s.st_dev,s.st_ino),json.loads(sys.argv[3]))))",
          path.join(root, "src"),
          directory,
          JSON.stringify(paths),
        ])
      ).stdout,
    );
  let child, closed, definition;
  const requests = [],
    job = { id: "input-witness-job", kind: "bash", status: "running" };
  const server = createServer(async (req, res) => {
    try {
      let reply;
      if (req.url === "/budget")
        reply = {
          turn_id: "4cd13570-125b-4f91-b50a-519ca5a5a80f",
          remaining_seconds: 120,
        };
      else if (req.url === "/workspace/checkpoint") {
        let body = "";
        for await (const part of req) body += part;
        const paths = JSON.parse(body).paths;
        requests.push(paths);
        reply = { ok: true, result: await checkpoint(paths) };
      } else throw new Error("Unexpected fixture endpoint");
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(reply));
    } catch (error) {
      res.writeHead(500);
      res.end(JSON.stringify({ error: error.message }));
    }
  });
  const previous = {
    url: process.env.DEEPSEEK_BASE_URL,
    key: process.env.DEEPSEEK_API_KEY,
  };
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  process.env.DEEPSEEK_BASE_URL = `http://127.0.0.1:${server.address().port}`;
  process.env.DEEPSEEK_API_KEY = "synthetic-local-input-check";
  try {
    await writeFile(path.join(directory, "source.txt"), "good");
    await writeFile(
      path.join(directory, "check.mjs"),
      `import{readFileSync,writeFileSync,existsSync}from'node:fs';
const tested=readFileSync('source.txt','utf8');writeFileSync('read.marker','read');
const end=Date.now()+4000;while(!existsSync('writer-done.marker')){if(Date.now()>end)throw Error('writer timeout');await new Promise(resolve=>setTimeout(resolve,10));}
writeFileSync('report.txt','generated output');if(tested!=='good')process.exit(1);
`,
    );
    const before = await checkpoint(["source.txt"]);
    apply({
      tools: {
        register(value) {
          definition = value;
        },
        async execute(call) {
          if (call.name === "bash") {
            assert.equal(call.arguments.command, "node check.mjs");
            child = spawn(process.execPath, ["check.mjs"], {
              cwd: directory,
              stdio: "ignore",
            });
            closed = new Promise((resolve, reject) => {
              child.once("error", reject);
              child.once("exit", (code) => {
                Object.assign(job, {
                  status: "completed",
                  detail: `exit code: ${code}`,
                });
                resolve(code);
              });
            });
            const deadline = Date.now() + 3000;
            let ready = false;
            while (Date.now() < deadline) {
              try {
                await access(path.join(directory, "read.marker"));
                ready = true;
                break;
              } catch {
                await new Promise((resolve) => setTimeout(resolve, 10));
              }
            }
            assert(
              ready,
              "The actual checker must read its input before the writer runs",
            );
            if (mutate)
              await writeFile(path.join(directory, "source.txt"), "bad");
            await writeFile(path.join(directory, "writer-done.marker"), "done");
            return {
              isError: false,
              value: { kind: "background", jobId: job.id },
            };
          }
          assert.equal(call.name, "job_output");
          await closed;
          return {
            isError: false,
            value: {
              job,
              text: "actual checker exited after the concurrent writer",
            },
          };
        },
      },
      jobs: {
        get() {
          return job;
        },
      },
    });
    const receipt = await definition.execute(
      {
        label: "Actual input checkpoint check",
        command: "node check.mjs",
        ...(strict ? { inputs: ["source.txt"] } : {}),
      },
      {
        agent: { id: "root" },
        callId: "actual-input-proof",
        token: {},
        signal: new AbortController().signal,
        deferContext() {},
      },
    );
    return {
      receipt,
      requests,
      exit: await closed,
      inputChanged: before.sha256 !== (await checkpoint(["source.txt"])).sha256,
      finalSource: await readFile(path.join(directory, "source.txt"), "utf8"),
      report: await readFile(path.join(directory, "report.txt"), "utf8"),
      finalSnapshot: await checkpoint(receipt.checkpoint.paths),
    };
  } finally {
    child?.kill();
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
    if (previous.url === undefined) delete process.env.DEEPSEEK_BASE_URL;
    else process.env.DEEPSEEK_BASE_URL = previous.url;
    if (previous.key === undefined) delete process.env.DEEPSEEK_API_KEY;
    else process.env.DEEPSEEK_API_KEY = previous.key;
  }
}
