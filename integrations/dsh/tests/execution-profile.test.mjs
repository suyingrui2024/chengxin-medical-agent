import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
  rm,
  realpath,
} from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { DeepSeekHarness } from "@deepseek-ai/dsh-sdk-client";
import { ensureExecutionProfileScope } from "../execution-profile.mjs";

test("an owned package scope restores real SDK plugin imports below an unreadable ancestor", async () => {
  const outer = await realpath(
    await mkdtemp(join(tmpdir(), "chengxin-profile-scope-")),
  );
  const state = join(outer, "state");
  const home = join(state, "home");
  const workspace = join(outer, "workspace");
  const ancestor = join(outer, "package.json");
  const preload = join(outer, "deny-parent.cjs");
  const require = createRequire(import.meta.url);
  const cli = join(
    dirname(require.resolve("@deepseek-ai/dsh/package.json")),
    "lib/bin.js",
  );
  try {
    await mkdir(home, { recursive: true });
    await mkdir(workspace);
    await writeFile(
      ancestor,
      JSON.stringify({ name: "external-host-package", private: true }),
    );
    // Reproduce Windows' visible-but-unreadable ancestor manifest without
    // relaxing native ACLs or requiring Windows for this loader regression.
    await writeFile(
      preload,
      `
      const fs = require("node:fs");
      const { syncBuiltinESMExports } = require("node:module");
      const original = fs.readFileSync;
      fs.readFileSync = function(path, ...args) {
        if (String(path) === ${JSON.stringify(ancestor)})
          throw Object.assign(new Error("Synthetic denied ancestor"), { code: "EACCES" });
        return original.call(this, path, ...args);
      };
      syncBuiltinESMExports();
    `,
    );
    const start = async () => {
      const harness = new DeepSeekHarness({
        dshBin: cli,
        profile: "sdk",
        dshHome: home,
        processCwd: workspace,
        cwd: workspace,
        provider: "deepseek-official",
        model: "fixture",
        initializeTimeoutMs: 20000,
        env: {
          PATH: process.env.PATH,
          ...(process.env.SystemRoot
            ? { SystemRoot: process.env.SystemRoot }
            : {}),
          DEEPSEEK_API_KEY: "profile-scope-loopback-only",
          DEEPSEEK_BASE_URL: "http://127.0.0.1:1/anthropic",
          NODE_OPTIONS:
            "--require=" + JSON.stringify(preload.replaceAll("\\", "/")),
        },
      });
      try {
        await harness.start(); // Initialize transport only; no model request.
      } finally {
        await harness.close();
      }
    };
    await assert.rejects(start(), /ERR_MODULE_NOT_FOUND/);
    await ensureExecutionProfileScope(state);
    const manifest = join(home, "profiles", "package.json");
    const original = await readFile(manifest, "utf8");
    await ensureExecutionProfileScope(state);
    assert.equal(await readFile(manifest, "utf8"), original);
    await start();
    assert.equal(
      JSON.parse(await readFile(ancestor, "utf8")).name,
      "external-host-package",
    );
  } finally {
    await rm(outer, { recursive: true, force: true });
  }
});
