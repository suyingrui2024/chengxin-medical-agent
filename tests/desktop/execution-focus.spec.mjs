import { _electron as electron, test, expect } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifyDirectoryRecovery } from "../fixtures/directory-recovery.mjs";
import { verifyArtifactRecovery } from "../fixtures/artifact-recovery.mjs";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
  verifyEditableAnswer,
  verifyExecutionRecovery,
  verifyIncompleteExplanation,
} from "../fixtures/execution-focus.mjs";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("native focused work area renders host snapshots and opens an inert preview", async () => {
  const profile = await mkdtemp(path.join(root, ".runtime/focus-native-"));
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: profile,
    MEDAGENT_BACKEND: "demo",
    MEDAGENT_DESKTOP_LIVE: "0",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const desktop = await electron.launch({ args: [root], env });
  try {
    const page = await desktop.firstWindow();
    await expect(page.getByText("本机服务已连接")).toBeVisible({
      timeout: 15000,
    });
    const fixture = await installExecutionFocusFixture(page);
    await openFocusWorkspace(page);
    await page
      .getByRole("button", { name: "快速查看：results.csv", exact: true })
      .click();
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    await verifyEditableAnswer(page, fixture);
    await verifyExecutionRecovery(page, fixture);
    await verifyDirectoryRecovery(page);
    await verifyIncompleteExplanation(page, fixture);
    await verifyArtifactRecovery(page);
    fixture.first.status = "completed";
    await expect(page.locator("#execution-focus-label")).toHaveText(
      "本轮已完成",
    );
    await page.locator("#execution-workspace").selectOption("focus-b");
    await expect(page.locator("#execution-focus-label")).toHaveText(
      "已保存的工作区",
    );
    await expect(page.locator("#execution-quick-look")).toBeHidden();
  } finally {
    await desktop.close();
    await rm(profile, { recursive: true, force: true });
  }
});
