import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { asset } from "../../src/medagent/web/js/structured-ui-primitives.js";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);
const PACKS = path.join(ROOT, "src", "medagent", "extensions", "packs");
const REGISTRY = path.join(
  ROOT,
  "src",
  "medagent",
  "extensions",
  "registry.py",
);

/**
 * The server's own rule, read out of the Python source so the two cannot
 * drift apart unnoticed: `ILLUSTRATION_PATH` in extensions/registry.py is
 * the pattern the capability registry accepts, and the frontend has to
 * accept exactly what the registry accepts.
 *
 * A stricter frontend rule is not a safe default. The path arrives from the
 * packs, and when the frontend rejects one the task card renders as "this
 * part cannot be displayed" -- the patient never sees the task at all.
 */
async function serverPattern() {
  const source = await readFile(REGISTRY, "utf8");
  const match = source.match(/ILLUSTRATION_PATH\s*=\s*r"([^"]+)"/);
  assert.ok(match, "registry.py no longer declares ILLUSTRATION_PATH");
  return new RegExp(`^${match[1]}(?:#step-[1-9][0-9]?)?$`);
}

const CORPUS = [
  "/body-location.svg",
  "/assets/illustrations/body-location.svg",
  "/assets/illustrations/neutral-temperature.svg",
  "/assets/illustrations/neutral-temperature.svg#step-1",
  "/assets/illustrations/neutral-temperature.svg#step-12",
  "/assets/fonts/a.svg",
  "/a/b/c/d.svg",
  "/assets/illustrations/body-location.svg#step-0",
  "/assets/illustrations/body-location.svg#step-100",
  "/assets/Illustrations/body-location.svg",
  "/assets/illustrations/body_location.svg",
  "/assets/illustrations/body-location.svg#other",
  "/assets/illustrations/body-location.png",
  "/assets/illustrations/../../core.py",
  "/assets/illustrations/.hidden.svg",
  "/assets/illustrations/body-location.svg/",
  "assets/illustrations/body-location.svg",
  "https://example.com/x.svg",
];

function accepts(fn, value) {
  try {
    fn(value);
    return true;
  } catch {
    return false;
  }
}

test("the frontend accepts exactly what the server's illustration rule accepts", async () => {
  const server = await serverPattern();
  for (const value of CORPUS) {
    const serverSays = server.test(value);
    const frontendSays = accepts(asset, value);
    assert.equal(
      frontendSays,
      serverSays,
      `frontend and server disagree about ${JSON.stringify(value)}: ` +
        `server ${serverSays ? "accepts" : "rejects"}, ` +
        `frontend ${frontendSays ? "accepts" : "rejects"}`,
    );
  }
});

test("every illustration a capability pack declares can be rendered", async () => {
  const files = (await readdir(PACKS)).filter((name) => name.endsWith(".json"));
  assert.ok(files.length > 0, "no capability packs found");

  const declared = [];
  const collect = (node) => {
    if (Array.isArray(node)) {
      node.forEach(collect);
      return;
    }
    if (!node || typeof node !== "object") return;
    for (const [key, value] of Object.entries(node)) {
      if (key === "asset" && typeof value === "string") declared.push(value);
      else if (key === "step_assets" && Array.isArray(value)) {
        declared.push(...value.filter((item) => typeof item === "string"));
      } else {
        collect(value);
      }
    }
  };
  for (const name of files) {
    collect(JSON.parse(await readFile(path.join(PACKS, name), "utf8")));
  }

  assert.ok(declared.length > 0, "no pack declares an illustration");
  for (const value of declared) {
    assert.doesNotThrow(
      () => asset(value),
      `the frontend cannot render ${value}, which a capability pack declares`,
    );
  }
});

test("an illustration path still cannot leave the served tree", () => {
  for (const value of [
    "/assets/illustrations/../../core.py",
    "/assets/illustrations/.hidden.svg",
    "/../secret.svg",
    "/assets/illustrations/body-location.svg#step-0",
    "https://example.com/x.svg",
  ]) {
    assert.throws(
      () => asset(value),
      new Error("Invalid local asset"),
      `${value} must not be renderable`,
    );
  }
});
