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
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
  verifyEditableAnswer,
  verifyExecutionRecovery,
} from "../../tests/fixtures/execution-focus.mjs";
import { verifyNativeArtifactChart } from "../../tests/fixtures/native-artifact-chart.mjs";
import { verifyDirectoryRecovery } from "../../tests/fixtures/directory-recovery.mjs";
import { verifyNativePatientZoom } from "../../tests/fixtures/patient-reflow.mjs";
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
  await reopened.keyboard.press("Escape");
  const beforeChart = model.requests.length;
  const chartReceipt = await verifyNativeArtifactChart(
    reopened,
    env.MEDAGENT_EXECUTION_ROOT,
  );
  assert.equal(model.requests.length, beforeChart);
  await reopened.locator("#execution-preview-copy").click();
  await expect(reopened.locator("#execution-preview-action-status")).toHaveText(
    "已复制读取时的原文",
  );
  assert.equal(
    (await app.evaluate(({ clipboard }) => clipboard.readText())).replace(
      /\r\n/g,
      "\n",
    ),
    "样本,质量 (g)\nA,1250\nB,2000\nC,750\n",
  );
  const savedSnapshot = join(scratch, "actual-csv.snapshot.txt");
  await app.evaluate(({ BrowserWindow }, filename) => {
    BrowserWindow.getAllWindows()[0].webContents.session.once(
      "will-download",
      (_event, item) => item.setSavePath(filename),
    );
  }, savedSnapshot);
  await reopened.locator("#execution-preview-save").click();
  await expect
    .poll(async () => {
      try {
        return await readFile(savedSnapshot, "utf8");
      } catch {
        return null;
      }
    })
    .toBe("样本,质量 (g)\nA,1250\nB,2000\nC,750\n");
  const focusFixture = await installExecutionFocusFixture(reopened);
  await openFocusWorkspace(reopened);
  await reopened
    .getByRole("button", { name: "快速查看：results.csv", exact: true })
    .click();
  await expect(reopened.locator("#execution-preview table")).toContainText(
    "1250",
  );
  const draftReceipt = await verifyEditableAnswer(reopened, focusFixture);
  const recoveryReceipt = await verifyExecutionRecovery(reopened, focusFixture);
  const directoryReceipt = await verifyDirectoryRecovery(reopened);
  assert.equal(model.requests.length, beforeChart);
  focusFixture.first.status = "completed";
  await expect(reopened.locator("#execution-focus-label")).toHaveText(
    "本轮已完成",
  );
  await reopened.locator("#execution-workspace").selectOption("focus-b");
  await expect(reopened.locator("#execution-focus-label")).toHaveText(
    "已保存的工作区",
  );
  await expect(reopened.locator("#execution-quick-look")).toBeHidden();
  const zoomReceipt = await verifyNativePatientZoom(reopened, (factor) =>
    app.evaluate(({ BrowserWindow }, value) => {
      const window = BrowserWindow.getAllWindows()[0];
      window.setSize(900, 680);
      window.webContents.setZoomFactor(value);
      return window.webContents.getZoomFactor();
    }, factor),
  );
  assert.equal(model.requests.length, beforeChart);
  console.log(
    JSON.stringify({
      gui: "passed",
      directory_recovery: directoryReceipt,
      patient_native_zoom: zoomReceipt,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      bundled_font_roles: fonts.map((row) => row.role),
      patient_calculation_and_resume: "passed",
      appearance_themes: themes,
      appearance_restart: "passed",
      execution_focus_ui: "passed (host snapshot fixture)",
      artifact_chart: chartReceipt,
      artifact_copy: "passed (actual CSV and system clipboard)",
      clipboard_line_endings: "OS-native; CRLF normalized only for comparison",
      artifact_save: "passed (actual CSV snapshot on disk)",
      clarification_draft: draftReceipt,
      execution_recovery: recoveryReceipt,
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
