import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  writeFile,
  symlink,
  rm,
  realpath,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, toNamespacedPath } from "node:path";
import { statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createPathResolver } from "../../windows/path-broker.mjs";
import runnerAdapter from "../../windows/runner-adapter.cjs";

test("path metadata rejects siblings, escaping links and undeclared files", async () => {
  const root = await mkdtemp(join(tmpdir(), "chengxin-path-"));
  try {
    const workspace = join(root, "workspace"),
      outside = join(root, "outside");
    await mkdir(workspace);
    await mkdir(outside);
    await writeFile(join(workspace, "中文.txt"), "fixture");
    await symlink(outside, join(workspace, "escape"), "junction");
    const selected = join(root, "single.json");
    await writeFile(selected, "{}");
    const lookup = createPathResolver([workspace, selected]);
    assert.equal(
      lookup(join(workspace, "中文.txt")).path,
      await realpath(join(workspace, "中文.txt")),
    );
    assert.equal(lookup(join(workspace, "missing.txt")).code, "ENOENT");
    assert.equal(lookup(outside).code, "EACCES");
    assert.equal(lookup(join(root, "workspace-sibling")).code, "EACCES");
    assert.equal(lookup(join(workspace, "escape")).code, "EACCES");
    assert.equal(lookup(selected).ok, true);
    assert.equal(lookup(join(root, "other.json")).code, "EACCES");
    assert.equal(lookup("relative").code, "EINVAL");
    assert.equal(lookup(workspace + "\0").code, "EINVAL");
    const alias = join(root, "selected-alias");
    await symlink(workspace, alias, "junction");
    const aliased = createPathResolver([alias]);
    assert.equal(aliased(join(workspace, "中文.txt")).ok, true);
    assert.equal(aliased(join(alias, "escape")).code, "EACCES");
    const manifestOnly = createPathResolver([
      { path: root, recursive: false },
      selected,
    ]);
    assert.equal(manifestOnly(root).ok, true);
    assert.equal(manifestOnly(selected).ok, true);
    assert.equal(manifestOnly(outside).code, "EACCES");
    assert.equal(manifestOnly(join(workspace, "中文.txt")).code, "EACCES");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("native realpath adapter preserves sync, callback, promise and error results", async () => {
  const root = await mkdtemp(join(tmpdir(), "chengxin-path-wire-"));
  const workspace = join(root, "workspace");
  await mkdir(workspace);
  const file = join(workspace, "中文.txt");
  await writeFile(file, "fixture");
  const privateFile = join(root, "private-marker.txt");
  await writeFile(privateFile, "synthetic-private-marker");
  const lookup = createPathResolver([workspace], { ancestorMetadata: true });
  assert.equal(lookup(root).ok, true);
  assert.equal(lookup(privateFile).code, "EACCES");
  assert.equal(lookup.statDirectory(file).code, "EACCES");
  const expectedInfo = statSync(root, { bigint: true });
  let requests = 0;
  const server = createServer(async (request, response) => {
    if (request.headers["x-api-key"] !== "path-fixture") {
      response.writeHead(401);
      response.end();
      return;
    }
    let raw = "";
    for await (const chunk of request) raw += chunk;
    requests++;
    const payload = JSON.parse(raw);
    response.end(
      JSON.stringify(
        payload.operation === "directory-stat"
          ? lookup.statDirectory(payload.path)
          : lookup(payload.path),
      ),
    );
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const adapter = fileURLToPath(
    new URL("../../windows/realpath-adapter.cjs", import.meta.url),
  );
  const code = `
    const fs=require('node:fs'),assert=require('node:assert/strict'),{promisify}=require('node:util');
    // Inject the precise OS-denial observed in Windows, without claiming this
    // host test runs a process container.
    const denied = new Set(${JSON.stringify([root, toNamespacedPath(root), privateFile])});
    const originalSync=fs.statSync, originalAsync=fs.promises.stat;
    fs.statSync=(path,...args)=>{if(denied.has(path))throw Object.assign(new Error('fixture OS denial'),{code:'EPERM'});return originalSync(path,...args);};
    fs.promises.stat=async(path,...args)=>{if(denied.has(path))throw Object.assign(new Error('fixture OS denial'),{code:'EPERM'});return originalAsync(path,...args);};
    require(${JSON.stringify(adapter)});
    (async()=>{
      const file=${JSON.stringify(file)}, expected=${JSON.stringify(await realpath(file))};
      assert.equal(fs.realpathSync.native(file),expected);
      assert.equal(fs.realpathSync(file),expected);
      assert.deepEqual(fs.realpathSync(file,{encoding:'buffer'}),Buffer.from(expected));
      assert.equal(await promisify(fs.realpath.native)(file),expected);
      assert.equal(await fs.promises.realpath(file),expected);
      assert.throws(()=>fs.realpathSync(${JSON.stringify(join(root, "outside"))}),{code:'EACCES'});
      await assert.rejects(fs.promises.realpath(${JSON.stringify(join(workspace, "missing"))}),{code:'ENOENT'});
      const normal=fs.statSync(${JSON.stringify(root)});
      const big=await fs.promises.stat(${JSON.stringify(toNamespacedPath(root))},{bigint:true});
      assert(normal instanceof fs.Stats); assert(normal.isDirectory());
      assert.equal(big.ino,BigInt(${JSON.stringify(String(expectedInfo.ino))}));
      assert.equal(big.mtimeNs,BigInt(${JSON.stringify(String(expectedInfo.mtimeNs))}));
      assert.equal(typeof normal.ino,'number'); assert(normal.mtime instanceof Date);
      assert.throws(()=>fs.statSync(${JSON.stringify(privateFile)}),{code:'EPERM'});
      console.log('passed');
    })().catch(error=>{console.error(error);process.exitCode=1;});`;
  let child;
  try {
    child = spawn(process.execPath, ["-e", code], {
      env: {
        ...process.env,
        CHENGXIN_PATH_BROKER: `http://127.0.0.1:${server.address().port}/metadata`,
        CHENGXIN_PATH_BROKER_AUTH: "path-fixture",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "",
      stderr = "";
    child.stdout.on("data", (value) => {
      stdout += value;
    });
    child.stderr.on("data", (value) => {
      stderr += value;
    });
    const timer = setTimeout(() => child.kill(), 15000);
    const exit = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", resolve);
    });
    clearTimeout(timer);
    assert.equal(exit, 0, stderr);
    assert.equal(stdout.trim(), "passed");
    assert.equal(requests, 10);
  } finally {
    child?.kill();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(root, { recursive: true, force: true });
  }
});

test("only the trusted Job runner receives the preload after NODE_OPTIONS is scrubbed", async () => {
  const root = await mkdtemp(join(tmpdir(), "chengxin-runner with spaces-"));
  const runner = join(root, "runner.cjs"),
    preload = join(root, "preload.cjs");
  let child;
  try {
    await writeFile(preload, "global.chengxinRuntimeReady = true;");
    await writeFile(
      runner,
      `process.send({ready:global.chengxinRuntimeReady, argv:process.argv.slice(2), hasProviderKey:Boolean(process.env.DEEPSEEK_API_KEY), nodeOptions:process.env.NODE_OPTIONS || ''}); process.disconnect();`,
    );
    const options = {
      env: {
        SystemRoot: process.env.SystemRoot,
        CHENGXIN_PATH_BROKER_AUTH: "metadata-only-fixture",
      },
      stdio: ["ignore", "pipe", "pipe", "ipc"],
      windowsHide: true,
    };
    let seen;
    const inspect = runnerAdapter.adaptRunnerSpawn(
      (...args) => {
        seen = args;
      },
      { node: process.execPath, runner, preload },
    );
    const ordinary = ["-e", "console.log(42)"];
    inspect(process.execPath, ordinary, options);
    assert.equal(seen[1], ordinary);
    assert.equal(seen[2], options);
    const prefixSibling = [runner + ".untrusted", "--"];
    inspect(process.execPath, prefixSibling, options);
    assert.equal(seen[1], prefixSibling);
    const arguments_ = [runner, "--", "command with spaces", "中文"];
    inspect(process.execPath, arguments_, options);
    assert.equal(seen[2], options);
    assert.deepEqual(arguments_, [runner, "--", "command with spaces", "中文"]);
    const adapted = runnerAdapter.adaptRunnerSpawn(spawn, {
      node: process.execPath,
      runner,
      preload,
    });
    child = adapted(process.execPath, arguments_, options);
    let stderr = "";
    child.stderr.on("data", (value) => {
      stderr += value;
    });
    const message = new Promise((resolve, reject) => {
      child.once("message", resolve);
      child.once("error", reject);
      child.once("exit", () =>
        reject(new Error("Runner exited without IPC: " + stderr)),
      );
    });
    const exit = new Promise((resolve) => child.once("exit", resolve));
    const timer = setTimeout(() => child.kill(), 15000);
    try {
      assert.deepEqual(await message, {
        ready: true,
        argv: arguments_.slice(1),
        hasProviderKey: false,
        nodeOptions: "",
      });
      assert.equal(await exit, 0, stderr);
    } finally {
      clearTimeout(timer);
    }
  } finally {
    child?.kill();
    await rm(root, { recursive: true, force: true });
  }
});
