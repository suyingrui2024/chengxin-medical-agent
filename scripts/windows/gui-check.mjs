// Native desktop check: a real Electron window with the application's sandbox enabled.
import { _electron as electron, expect } from "@playwright/test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import {
  createGlucoseModel,
  verifyGlucoseCalculation,
} from "../../tests/fixtures/glucose-calculation.mjs";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const scratch = await mkdtemp(join(tmpdir(), "chengxin native gui 中文-"));
const model = await createGlucoseModel();
let app;
try {
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: join(scratch, "profile"),
    MEDAGENT_EXECUTION_ROOT: join(scratch, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "compatible",
    MEDAGENT_LLM_BASE_URL: model.endpoint,
    MEDAGENT_LLM_MODEL: "fixture",
    MEDAGENT_LLM_API_KEY: "windows-local-fixture-only",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  const launch = () =>
    electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env,
      timeout: 30000,
    });
  app = await launch();
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
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].setSize(900, 760),
  );
  const appearanceFile = join(env.MEDAGENT_DESKTOP_PROFILE, "appearance.json");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "forest");
  await page.getByRole("button", { name: "外观与皮肤", exact: true }).click();
  const themes = ["ocean", "iris", "sand", "slate", "forest", "night"];
  for (const theme of themes) {
    await page.locator(`input[name="appearance"][value="${theme}"]`).check();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.locator("#appearance-status")).toContainText(
      "下次打开会保留",
    );
    await expect
      .poll(async () => JSON.parse(await readFile(appearanceFile, "utf8")))
      .toEqual({ schema: 1, value: theme });
  }
  await page.keyboard.press("Escape");
  await verifyGlucoseCalculation(page, model, { native: true });
  await app.close();
  app = undefined;
  app = await launch();
  const reopened = await app.firstWindow();
  await expect(reopened.getByText("本机服务已连接")).toBeVisible({
    timeout: 30000,
  });
  await expect(reopened.locator("html")).toHaveAttribute("data-theme", "night");
  await reopened
    .getByRole("button", { name: "外观与皮肤", exact: true })
    .click();
  await expect(
    reopened.locator('input[name="appearance"][value="night"]'),
  ).toBeChecked();
  console.log(
    JSON.stringify({
      gui: "passed",
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      bundled_font_roles: fonts.map((row) => row.role),
      patient_calculation_and_resume: "passed",
      appearance_themes: themes,
      appearance_restart: "passed",
      profile_path_with_spaces_and_chinese: "passed",
      local_fixture_requests: model.requests.length,
      external_model_calls: 0,
    }),
  );
} finally {
  if (app) await app.close();
  await model.close();
  await rm(scratch, { recursive: true, force: true });
}
