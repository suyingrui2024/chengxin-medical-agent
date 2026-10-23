const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  migrateLegacyAppearance,
  readAppearance,
  saveAppearance,
} = require("./appearance.cjs");

function scratch() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "chengxin-appearance-"));
}

function writeAppearance(directory, value, mtime) {
  fs.mkdirSync(directory, { recursive: true });
  const target = path.join(directory, "appearance.json");
  fs.writeFileSync(target, JSON.stringify({ schema: 1, value }) + "\n");
  if (mtime) fs.utimesSync(target, mtime, mtime);
  return target;
}

test("migration copies the newest legacy appearance and keeps the old dirs", () => {
  const appData = scratch();
  const userData = path.join(appData, "chengxin-medagent");
  try {
    const older = path.join(appData, "chengxin-medagent-0.9.0");
    const newer = path.join(appData, "chengxin-medagent-1.0.0");
    writeAppearance(older, "ocean", new Date("2026-01-01T00:00:00Z"));
    writeAppearance(newer, "night", new Date("2026-06-01T00:00:00Z"));

    assert.equal(migrateLegacyAppearance(appData, userData), true);
    assert.equal(readAppearance(userData), "night");
    // Conservative by design: legacy directories are left in place.
    assert.equal(readAppearance(older), "ocean");
    assert.equal(readAppearance(newer), "night");
  } finally {
    fs.rmSync(appData, { recursive: true, force: true });
  }
});

test("migration is a no-op when the current directory already has appearance", () => {
  const appData = scratch();
  const userData = path.join(appData, "chengxin-medagent");
  try {
    writeAppearance(path.join(appData, "chengxin-medagent-1.0.0"), "night");
    saveAppearance(userData, "sand");
    assert.equal(migrateLegacyAppearance(appData, userData), false);
    assert.equal(readAppearance(userData), "sand");
  } finally {
    fs.rmSync(appData, { recursive: true, force: true });
  }
});

test("migration ignores unrelated directories and missing appearance files", () => {
  const appData = scratch();
  const userData = path.join(appData, "chengxin-medagent");
  try {
    fs.mkdirSync(path.join(appData, "chengxin-medagent-1.0.0"), {
      recursive: true,
    });
    writeAppearance(path.join(appData, "someone-else"), "night");
    assert.equal(migrateLegacyAppearance(appData, userData), false);
    assert.equal(fs.existsSync(userData), false);
    assert.equal(readAppearance(userData), "forest");
  } finally {
    fs.rmSync(appData, { recursive: true, force: true });
  }
});
