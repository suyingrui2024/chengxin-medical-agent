// Native boundary probe with a loopback fixture, never a provider API.
import assert from "node:assert/strict";
import { nativeLaunch } from "../../integrations/windows/isolation.mjs";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  rm,
  unlink,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createServer } from "node:http";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
if (process.platform !== "win32")
  throw new Error("This check requires real Windows");
const scratch = await mkdtemp(join(tmpdir(), "chengxin-native-isolation-"));
const gateway = createServer((_req, res) => res.end("fixture-gateway"));
let launch;
try {
  const workspace = join(scratch, "workspace"),
    state = join(scratch, "state");
  await mkdir(workspace);
  await mkdir(state);
  const marker = join(scratch, "host-only-secret.txt");
  await writeFile(marker, "host-only-secret-marker");
  await new Promise((resolve) => gateway.listen(0, "127.0.0.1", resolve));
  const python = process.env.MEDAGENT_PYTHON;
  assert(python, "The installation must select the dedicated Windows Python");
  const code = `const fs=require('node:fs'),cp=require('node:child_process'),assert=require('node:assert/strict');
    assert.throws(()=>fs.readFileSync(${JSON.stringify(marker)}));
    assert.equal(process.env.CHENGXIN_HOST_ONLY_SECRET,undefined);
    fs.writeFileSync('artifact.txt','native-isolated');
    assert.equal(cp.execFileSync(${JSON.stringify(python)},['-I','-c','print(6*7)'],{encoding:'utf8'}).trim(),'42');
    fetch(${JSON.stringify(`http://127.0.0.1:${gateway.address().port}`)}).then(r=>r.text()).then(text=>{assert.equal(text,'fixture-gateway');console.log(JSON.stringify({passed:true,host_file_denied:true,host_env_hidden:true,code_executed:true,host_gateway_reachable:true}));}).catch(()=>process.exit(1));`;
  launch = nativeLaunch({
    workspace,
    state,
    modules: join(root, "node_modules"),
    integration: join(root, "integrations/dsh"),
    env: { CHENGXIN_PYTHON: python },
    command: [process.execPath, "-e", code],
    readonly: [dirname(process.execPath), dirname(python)],
  });
  const { stdout } = await promisify(execFile)(launch.command, launch.args, {
    encoding: "utf8",
    timeout: 45000,
    maxBuffer: 128000,
    env: {
      SystemRoot: process.env.SystemRoot,
      PATH: process.env.PATH,
      TEMP: process.env.TEMP,
      CHENGXIN_HOST_ONLY_SECRET: "must-not-leak",
    },
  });
  const result = JSON.parse(stdout.trim());
  assert.equal(result.passed, true);
  assert.equal(
    await readFile(join(workspace, "artifact.txt"), "utf8"),
    "native-isolated",
  );
  console.log(
    JSON.stringify({
      isolation: "windows-processcontainer",
      ...result,
      model_calls: 0,
    }),
  );
} catch (error) {
  console.error("Native isolation check failed:", error.message);
  if (error.cause)
    console.error(
      "Probe:",
      error.cause.message,
      error.cause.stdout || "",
      error.cause.stderr || "",
    );
  throw error;
} finally {
  if (launch?.policyFile) await unlink(launch.policyFile).catch(() => {});
  gateway.closeAllConnections();
  await new Promise((resolve) => gateway.close(resolve));
  await rm(scratch, { recursive: true, force: true });
}
