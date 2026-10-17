// Evaluation-only bounded oracle. Candidate code runs only inside the same bwrap mounts.
import { execFile } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { isolatedLaunch } from "../integrations/dsh/isolated-entry.mjs";

let input = "";
for await (const chunk of process.stdin) {
  input += chunk;
  if (Buffer.byteLength(input) > 2_000_000)
    throw new Error("Oracle input exceeds bound");
}
const payload = JSON.parse(input);
if (
  !Number.isInteger(payload.timeout_ms) ||
  payload.timeout_ms < 1000 ||
  payload.timeout_ms > 300000 ||
  typeof payload.code !== "string" ||
  !["python3", "python39"].includes(payload.interpreter)
)
  throw new Error("Invalid oracle budget/code");
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const launch = isolatedLaunch({
  workspace: payload.workspace,
  state: payload.state,
  nodeRoot: resolve(dirname(realpathSync(process.execPath)), ".."),
  modules: resolve(root, "node_modules"),
  integration: resolve(root, "integrations/dsh"),
  env: {
    MPLBACKEND: "agg",
    OMP_NUM_THREADS: "1",
    OPENBLAS_NUM_THREADS: "1",
    MKL_NUM_THREADS: "1",
  },
  argv: [],
  command: [
    payload.interpreter === "python39"
      ? "/workspace/.benchmark_dependencies/_python39/python/bin/python3.9"
      : "/usr/bin/python3",
    "-",
  ],
});
const child = execFile(
  launch.command,
  launch.args,
  {
    env: { PATH: "/usr/bin:/bin" },
    timeout: payload.timeout_ms,
    maxBuffer: 4_000_000,
  },
  (error, stdout, stderr) => {
    process.stdout.write(
      JSON.stringify({
        passed: !error,
        stdout,
        stderr,
        exit_code: error?.code || 0,
        signal: error?.signal || null,
      }) + "\n",
    );
    process.exitCode = error ? 1 : 0;
  },
);
child.stdin.on("error", () => {});
child.stdin.end(payload.code);
