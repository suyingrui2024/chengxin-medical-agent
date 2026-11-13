import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));

test("relocated Playwright configs resolve all paths from the project, not cwd", async () => {
  const before = process.cwd();
  try {
    process.chdir(tmpdir());
    const { default: ui } =
      await import("../../config/playwright/ui.config.mjs");
    const { default: desktop } =
      await import("../../config/playwright/desktop.config.mjs");
    const { default: live } =
      await import("../../config/playwright/live.config.mjs");
    assert.equal(ui.testDir, path.join(root, "tests/ui"));
    assert.equal(ui.webServer.cwd, root);
    assert.equal(desktop.testDir, path.join(root, "tests/desktop"));
    assert.equal(live.testDir, path.join(root, "tests/live"));
    for (const config of [ui, desktop, live]) {
      assert.equal(path.isAbsolute(config.outputDir), true);
      assert.equal(
        config.outputDir.startsWith(path.join(root, "test-results")),
        true,
      );
    }
    const { default: entry } = await import("../../playwright.config.mjs");
    assert.equal(entry, ui);
  } finally {
    process.chdir(before);
  }
});
