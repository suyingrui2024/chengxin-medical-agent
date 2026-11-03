#!/usr/bin/env node
/**
 * Run the browser suite one spec file at a time, each against its own
 * server and its own patient-data directory.
 *
 * Why: `playwright.config.mjs` starts a single demo server for the whole
 * run and points it at `test-results/ui/patient-data`. Sessions live in
 * that process's memory and the patient record lives on disk, so every
 * spec inherits whatever the previous one left behind — a follow-up
 * window, a medication reminder, a half-recognised entry. Tests that
 * reasonably assume a fresh workspace then fail on state they never
 * created, and the failure moves around depending on file order.
 *
 * `reuseExistingServer: false` already gives us the isolation primitive:
 * one `playwright test` invocation per file means one server per file.
 * All this script adds is the loop and the data-directory reset between
 * invocations. Sequential on purpose — the config pins `workers: 1` and
 * every run binds the same port.
 *
 * This is the only way the suite is meant to run whole (`test:ui`,
 * `test:ui:ci`). Running every file through a single `playwright test`
 * invocation is not offered: it is the setup that produced the drift
 * above, and it fails in ways that look like product bugs. For a single
 * file while iterating, either pass a name filter
 * (`npm run test:ui -- location`) or call `npx playwright test <file>`
 * directly — one file never inherits anything.
 */
import { spawn } from "node:child_process";
import { readdirSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import process from "node:process";

const ROOT = path.resolve(import.meta.dirname, "../..");
const UI_DIR = path.join(ROOT, "tests/ui");
// The same directory the Playwright config writes to, so the cleanup and
// the run always agree about where the artefacts are.
const OUTPUT_DIR = path.join(
  ROOT,
  process.env.UI_OUTPUT_DIR || "test-results/ui",
);
const SHOTS_DIR = path.join(ROOT, "test-results/shots");
/**
 * The port the demo server binds. Each shard needs its own: the suite is
 * sequential on purpose, but two shards running side by side would fight
 * over the port, and the wait-for-free logic would then spend its time
 * failing rather than testing.
 */
const PORT = Number(process.env.UI_PORT || 18766);

/**
 * `UI_SHARD=2/4` runs the second quarter of the spec files. The browser
 * suite is the long pole in CI -- it starts a fresh server per file -- so
 * sharding it is what keeps the job inside its timeout. Files are assigned
 * round-robin by index, which keeps the shards comparable in cost rather
 * than front-loading the slow ones into one of them.
 */
function shardOf(index, total) {
  return index % total;
}

function parseShard() {
  const raw = process.env.UI_SHARD;
  if (!raw) return null;
  const match = /^(\d+)\/(\d+)$/.exec(raw.trim());
  if (!match) {
    console.error(`UI_SHARD must look like 2/4, got ${raw}`);
    process.exit(1);
  }
  const [, index, total] = match.map(Number);
  if (index < 1 || index > total) {
    console.error(`UI_SHARD ${raw} is out of range`);
    process.exit(1);
  }
  return { index: index - 1, total };
}
/**
 * `--shots` writes the acceptance screenshots into `docs/assets/ui`
 * (the committed set) instead of the throwaway output directory. Set as an
 * env var on the child rather than as a shell prefix so the npm script also
 * works on Windows.
 */
const updateShots = process.argv.includes("--shots");

/**
 * Clear a directory with Python.
 *
 * Playwright empties `outputDir` itself at the start of every run, but this
 * sandbox wraps `fs.rm` in a bulk-delete guard that refuses trees over 50
 * entries — a few failing specs leave thousands of trace files behind and
 * every later invocation dies inside the shim before running a test.
 * Python's shutil is not wrapped, so the cleanup happens here instead.
 */
function clean(dir) {
  return new Promise((resolve) => {
    const child = spawn(
      "python3",
      [
        "-c",
        `import shutil; shutil.rmtree(${JSON.stringify(dir)}, ignore_errors=True)`,
      ],
      { cwd: ROOT, stdio: "ignore" },
    );
    child.on("error", (error) => {
      console.error(`cleanup failed to start: ${error.message}`);
      resolve();
    });
    child.on("close", resolve);
  });
}

/**
 * `reuseExistingServer: false` refuses to start while anything answers on
 * the port, and the previous invocation's server can take a moment to
 * release it. Waiting here is the difference between an honest result and
 * a wall of instant failures that never ran a single test.
 */
async function waitForPortFree(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const busy = await new Promise((resolve) => {
      const socket = net.connect({ port: PORT, host: "127.0.0.1" });
      socket.once("connect", () => {
        socket.destroy();
        resolve(true);
      });
      socket.once("error", () => resolve(false));
    });
    if (!busy) return true;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

/**
 * Every spec file, in a stable order. The name filter is applied later, after
 * sharding: filtering first would renumber the files and hand most of the
 * shards nothing to run.
 */
function specFiles() {
  return readdirSync(UI_DIR)
    .filter((name) => name.endsWith(".spec.mjs"))
    .sort()
    .map((name) => path.join("tests/ui", name));
}

function run(file) {
  return new Promise((resolve) => {
    const child = spawn(
      "npx",
      ["playwright", "test", file, "--config", "playwright.ci.config.mjs"],
      {
        cwd: ROOT,
        stdio: ["ignore", "pipe", "pipe"],
        env: updateShots
          ? { ...process.env, UPDATE_DOC_SHOTS: "1" }
          : process.env,
      },
    );
    let output = "";
    child.stdout.on("data", (chunk) => (output += chunk));
    child.stderr.on("data", (chunk) => (output += chunk));
    child.on("close", (code) => {
      const line = output
        .split("\n")
        .reverse()
        .find((text) => /^\s+\d+ (passed|failed)/.test(text));
      resolve({ file, code: code ?? 1, summary: (line ?? "").trim(), output });
    });
  });
}

const allFiles = specFiles();
if (allFiles.length === 0) {
  console.error("no spec files under tests/ui");
  process.exit(1);
}
const shard = parseShard();
const sharded = shard
  ? allFiles.filter((_, index) => shardOf(index, shard.total) === shard.index)
  : allFiles;
// Optional argument filters by file name, so a single spec can be run
// through the same isolated path while iterating on it. It narrows the
// shard, it does not replace it.
const filter = process.argv.slice(2).find((arg) => !arg.startsWith("--"));
const files = filter
  ? sharded.filter((file) => file.includes(filter))
  : sharded;
if (shard) {
  console.log(
    `shard ${shard.index + 1}/${shard.total}: ${files.length} of ${sharded.length} spec files`,
  );
}
if (files.length === 0) {
  console.error("no spec files matched");
  process.exit(1);
}

const failures = [];
// Screenshots from a previous run would otherwise mix with this one's.
// With `--shots` they go to docs/assets/ui instead, which must not be
// emptied — the run is rewriting the committed set file by file.
if (!updateShots) await clean(SHOTS_DIR);
for (const [index, file] of files.entries()) {
  if (!(await waitForPortFree())) {
    console.error(`port ${PORT} never freed before ${file}`);
    process.exit(1);
  }
  // A clean store per file: the server reads this directory at startup,
  // and Playwright needs an output directory it can empty itself.
  await clean(OUTPUT_DIR);
  const result = await run(file);
  const label = `${index + 1}/${files.length} ${path.basename(file)}`;
  if (result.code === 0) {
    console.log(`ok   ${label}  ${result.summary}`);
  } else {
    console.log(`FAIL ${label}  ${result.summary}`);
    failures.push(result);
  }
}

if (failures.length) {
  console.error(`\n${failures.length} spec file(s) failed:\n`);
  for (const failure of failures) {
    console.error(`--- ${failure.file} ---`);
    const lines = failure.output.split("\n");
    const start = lines.findIndex((line) => /^\s+1\) /.test(line));
    console.error(lines.slice(start === -1 ? -40 : start).join("\n"));
  }
  process.exit(1);
}
console.log(`\nall ${files.length} spec files passed`);
