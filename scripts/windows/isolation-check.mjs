// Native boundary probe with a loopback fixture, never a provider API.
import assert from "node:assert/strict";
import { nativeLaunch } from "../../integrations/windows/isolation.mjs";
import { nativePowerShellSource } from "../../integrations/windows/powershell-command.mjs";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  realpath,
  rm,
  unlink,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
if (process.platform !== "win32")
  throw new Error("This check requires real Windows");
// Windows' TEMP may use an 8.3 alias (RUNNER~1). Match the canonical paths
// supplied by ExecutionManager; PowerShell providers cannot traverse the alias
// under a policy granting only the physical directory.
const scratch = await realpath(
  await mkdtemp(join(tmpdir(), "chengxin-native-isolation-")),
);
const gateway = createServer((_req, res) => res.end("fixture-gateway"));
let launch;
const applicationSecret = join(root, `.native-host-only-${randomUUID()}.txt`);
try {
  await writeFile(applicationSecret, "synthetic-private-application-marker", {
    flag: "wx",
  });
  const workspace = join(scratch, "workspace"),
    state = join(scratch, "state");
  await mkdir(workspace);
  await mkdir(state);
  const marker = join(scratch, "host-only-secret.txt");
  await writeFile(marker, "host-only-secret-marker");
  const readOnly = join(scratch, "readonly");
  await mkdir(readOnly);
  await writeFile(join(readOnly, "reference.txt"), "immutable-reference");
  await new Promise((resolve) => gateway.listen(0, "127.0.0.1", resolve));
  const python = process.env.MEDAGENT_PYTHON;
  assert(python, "The installation must select the dedicated Windows Python");
  const nativePipeline = nativePowerShellSource({
    workdir: workspace,
    command: "python.exe -I -c 'print(42)' | ForEach-Object { $_ }",
  });
  const nativeFailure = nativePowerShellSource({
    workdir: workspace,
    command: "python.exe -I -c 'raise SystemExit(23)'",
  });
  const pathProbe = `import ctypes as c,json,sys
from ctypes import wintypes as w
k=c.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE];k.CreateFileW.restype=w.HANDLE
k.GetFinalPathNameByHandleW.argtypes=[w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD];k.GetFinalPathNameByHandleW.restype=w.DWORD
k.CloseHandle.argtypes=[w.HANDLE]
rows=[]
for path in sys.argv[1:]:
 for access in [0,128,2147483648]:
  h=k.CreateFileW(path,access,7,None,3,0x02000000,None)
  row={'path':path,'access':access,'open_error':c.get_last_error() if h==c.c_void_p(-1).value else 0}
  if not row['open_error']:
   for flags in [0,8,2,10]:
    b=c.create_unicode_buffer(32768);n=k.GetFinalPathNameByHandleW(h,b,len(b),flags)
    row[str(flags)]={'value':b.value if n else '', 'error':0 if n else c.get_last_error()}
   k.CloseHandle(h)
  rows.append(row)
print(json.dumps(rows))`;
  const code = `const fs=require('node:fs'),cp=require('node:child_process'),assert=require('node:assert/strict');
    console.error(cp.execFileSync(${JSON.stringify(python)},['-I','-c',${JSON.stringify(pathProbe)},${JSON.stringify(join(root, "node_modules/@deepseek-ai/dsh"))},${JSON.stringify(workspace)}],{encoding:'utf8'}).trim());
    assert.throws(()=>fs.readFileSync(${JSON.stringify(marker)}));
    assert.throws(()=>fs.readFileSync(${JSON.stringify(applicationSecret)}));
    assert.equal(typeof JSON.parse(fs.readFileSync(${JSON.stringify(join(root, "package.json"))},'utf8')).name,'string');
    assert.equal(process.env.CHENGXIN_HOST_ONLY_SECRET,undefined);
    assert.equal(process.env.PATHEXT,'.COM;.EXE;.BAT;.CMD');
    assert.equal(fs.readFileSync(${JSON.stringify(join(readOnly, "reference.txt"))},'utf8'),'immutable-reference');
    assert.throws(()=>fs.writeFileSync(${JSON.stringify(join(readOnly, "reference.txt"))},'must-not-write'));
    fs.writeFileSync('artifact.txt','native-isolated');
    assert.equal(cp.execFileSync(${JSON.stringify(python)},['-I','-c','print(6*7)'],{encoding:'utf8'}).trim(),'42');
    assert.equal(cp.execFileSync(require('node:path').join(process.env.SystemRoot,'System32/WindowsPowerShell/v1.0/powershell.exe'),['-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',${JSON.stringify(Buffer.from(nativePipeline, "utf16le").toString("base64"))}],{encoding:'utf8'}).trim(),'42');
    const failedNative=cp.spawnSync(require('node:path').join(process.env.SystemRoot,'System32/WindowsPowerShell/v1.0/powershell.exe'),['-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',${JSON.stringify(Buffer.from(nativeFailure, "utf16le").toString("base64"))}],{encoding:'utf8'});
    assert.equal(failedNative.status,23,failedNative.stderr);
    cp.execFileSync(${JSON.stringify(python)},['-I','-m','venv','.venv'],{encoding:'utf8'});
    const projectPython=require('node:path').resolve('.venv/Scripts/python.exe');
    assert.equal(cp.execFileSync(projectPython,['-c','import sys; assert sys.prefix != sys.base_prefix; print(42)'],{encoding:'utf8'}).trim(),'42');
    assert.match(cp.execFileSync(projectPython,['-m','pip','--version'],{encoding:'utf8'}),/^pip /);
    fetch(${JSON.stringify(`http://127.0.0.1:${gateway.address().port}`)}).then(r=>r.text()).then(text=>{assert.equal(text,'fixture-gateway');console.log(JSON.stringify({passed:true,host_file_denied:true,host_env_hidden:true,code_executed:true,host_gateway_reachable:true}));}).catch(()=>process.exit(1));`;
  launch = nativeLaunch({
    workspace,
    state,
    modules: join(root, "node_modules"),
    integration: join(root, "integrations/dsh"),
    env: { CHENGXIN_PYTHON: python },
    command: [process.execPath, "-e", code],
    readonly: [dirname(process.execPath), dirname(python), readOnly],
  });
  const { stdout, stderr } = await promisify(execFile)(
    launch.command,
    launch.args,
    {
      encoding: "utf8",
      timeout: 45000,
      maxBuffer: 128000,
      env: {
        SystemRoot: process.env.SystemRoot,
        PATH: process.env.PATH,
        TEMP: process.env.TEMP,
        CHENGXIN_HOST_ONLY_SECRET: "must-not-leak",
        PATHEXT: ".PRIVATE_HOST_EXTENSION",
      },
    },
  );
  if (stderr.trim()) console.log("Native path diagnostics:", stderr.trim());
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
      powershell_native_pipeline: "passed",
      powershell_native_exit_status: "passed",
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
  if (error.cause?.probe)
    console.error("Native host facts:", JSON.stringify(error.cause.probe));
  throw error;
} finally {
  if (launch?.policyFile) await unlink(launch.policyFile).catch(() => {});
  await unlink(applicationSecret).catch(() => {});
  gateway.closeAllConnections();
  await new Promise((resolve) => gateway.close(resolve));
  await rm(scratch, { recursive: true, force: true });
}
