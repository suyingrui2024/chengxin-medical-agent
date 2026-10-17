// Independent evaluator: run a host-selected check inside the same mount policy.
import { isolatedLaunch } from "./isolated-entry.mjs";
import { execFile } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
let input = "";
for await (const chunk of process.stdin) {
  input += chunk;
  if (input.length > 64000) throw new Error("Check input too large");
}
const data = JSON.parse(input);
const integration = dirname(fileURLToPath(import.meta.url));
const launch = isolatedLaunch({
  workspace: data.workspace,
  state: data.state,
  nodeRoot: resolve(dirname(realpathSync(process.execPath)), ".."),
  modules: resolve(integration, "../../node_modules"),
  integration,
  env: {},
  argv: [],
  command: ["/usr/bin/python3", "-c", data.code],
});
execFile(
  launch.command,
  launch.args,
  { env: { PATH: "/usr/bin:/bin" }, timeout: 15000, maxBuffer: 256000 },
  (error, stdout, stderr) => {
    process.stdout.write(
      JSON.stringify({
        passed: !error,
        stdout,
        stderr,
        exit_code: error?.code || 0,
      }) + "\n",
    );
    process.exitCode = error ? 1 : 0;
  },
);
