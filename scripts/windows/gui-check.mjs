// Native desktop check: a real Electron window with the application's sandbox enabled.
import { _electron as electron } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const scratch = await mkdtemp(join(tmpdir(), "chengxin-native-gui-"));
let app;
try {
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: join(scratch, "profile"),
    MEDAGENT_EXECUTION_ROOT: join(scratch, "workspaces"),
  };
  delete env.MEDAGENT_DESKTOP_LIVE;
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  app = await electron.launch({
    executablePath,
    args: executablePath ? [] : [root],
    env,
    timeout: 30000,
  });
  const page = await app.firstWindow();
  await page.waitForURL(/http:\/\/127\.0\.0\.1:\d+\//, { timeout: 30000 });
  await page.locator("body").waitFor();
  const preferences = await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences(),
  );
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.nodeIntegration, false);
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  const { checkBundledFonts } = await import("../check-bundled-fonts.mjs");
  const fonts = await checkBundledFonts(page);
  console.log(
    JSON.stringify({
      gui: "passed",
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      bundled_font_roles: fonts.map((row) => row.role),
      model_calls: 0,
    }),
  );
} finally {
  if (app) await app.close();
  await rm(scratch, { recursive: true, force: true });
}
