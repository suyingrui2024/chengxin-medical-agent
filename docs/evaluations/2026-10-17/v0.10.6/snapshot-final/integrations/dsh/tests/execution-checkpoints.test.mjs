import { test } from "node:test";
import assert from "node:assert/strict";
import {
  checkpointPaths,
  mergeCheckpointPaths,
  coversCheckpointPaths,
  validCheckpoint,
} from "../execution-checkpoints.mjs";

test("checkpoint scopes keep prior inputs and reject traversal before any scan", () => {
  assert.deepEqual(checkpointPaths(), ["."]);
  assert.deepEqual(checkpointPaths(["src/a.py", "tests", "src"]), [
    "src",
    "tests",
  ]);
  assert.deepEqual(mergeCheckpointPaths(["src", "tests"], ["src"]), [
    "src",
    "tests",
  ]);
  assert.deepEqual(mergeCheckpointPaths(["."], ["src"]), ["."]);
  assert.equal(coversCheckpointPaths(["src"], ["src/a.py"]), true);
  assert.equal(coversCheckpointPaths(["src/a.py"], ["src"]), false);
  for (const paths of [
    [],
    ["../secret"],
    ["/etc"],
    ["src/../secret"],
    ["a\\b"],
    [".", "../secret"],
  ])
    assert.throws(() => checkpointPaths(paths));
});

test("only complete canonical checkpoint metadata carries a proof digest", () => {
  const value = {
    schema: "workspace-checkpoint/v1",
    paths: ["src"],
    complete: true,
    sha256: "a".repeat(64),
    files: 1,
    ignored_directories: ["__pycache__"],
    symlink_targets_followed: false,
  };
  assert.equal(validCheckpoint(value), true);
  assert.equal(
    validCheckpoint({ ...value, complete: false, sha256: null }),
    true,
  );
  for (const changes of [
    { complete: false },
    { files: -1 },
    { files: 20001 },
    { paths: ["../secret"] },
    { paths: ["src/a", "src"] },
    { sha256: "passed" },
    { symlink_targets_followed: true },
  ])
    assert.equal(validCheckpoint({ ...value, ...changes }), false);
});

test("a parent or root scope cannot erase an explicitly watched cache input", () => {
  assert.deepEqual(mergeCheckpointPaths(["node_modules/pkg"], ["."]), [
    ".",
    "node_modules/pkg",
  ]);
  assert.deepEqual(
    mergeCheckpointPaths(["src/__pycache__/evidence.json"], ["src"]),
    ["src", "src/__pycache__/evidence.json"],
  );
  assert.equal(coversCheckpointPaths(["."], ["node_modules/pkg"]), false);
  assert.equal(
    coversCheckpointPaths(
      [".", "node_modules/pkg"],
      ["node_modules/pkg/index.js"],
    ),
    true,
  );
});

test("scope order uses Unicode scalar order consistently with the Python host", () => {
  assert.deepEqual(checkpointPaths(["🧪检验", "Ａ报告"]), ["Ａ报告", "🧪检验"]);
});
