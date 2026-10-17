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
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createPathResolver } from "../../windows/path-broker.mjs";

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
  const lookup = createPathResolver([workspace]);
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
    response.end(JSON.stringify(lookup(JSON.parse(raw).path)));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const adapter = fileURLToPath(
    new URL("../../windows/realpath-adapter.cjs", import.meta.url),
  );
  const code = `
    const fs=require('node:fs'),assert=require('node:assert/strict'),{promisify}=require('node:util');
    (async()=>{
      const file=${JSON.stringify(file)}, expected=${JSON.stringify(await realpath(file))};
      assert.equal(fs.realpathSync.native(file),expected);
      assert.equal(fs.realpathSync(file),expected);
      assert.deepEqual(fs.realpathSync(file,{encoding:'buffer'}),Buffer.from(expected));
      assert.equal(await promisify(fs.realpath.native)(file),expected);
      assert.equal(await fs.promises.realpath(file),expected);
      assert.throws(()=>fs.realpathSync(${JSON.stringify(join(root, "outside"))}),{code:'EACCES'});
      await assert.rejects(fs.promises.realpath(${JSON.stringify(join(workspace, "missing"))}),{code:'ENOENT'});
      console.log('passed');
    })().catch(error=>{console.error(error);process.exitCode=1;});`;
  let child;
  try {
    child = spawn(process.execPath, ["--require", adapter, "-e", code], {
      env: {
        ...process.env,
        CHENGXIN_PATH_BROKER: `http://127.0.0.1:${server.address().port}/metadata`,
        DEEPSEEK_API_KEY: "path-fixture",
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
    assert.equal(requests, 7);
  } finally {
    child?.kill();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(root, { recursive: true, force: true });
  }
});
