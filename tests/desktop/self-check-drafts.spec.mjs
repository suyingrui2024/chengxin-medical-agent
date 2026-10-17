import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  verifyLocationDraft,
  verifyTemperatureProgress,
} from "../fixtures/self-check-drafts.mjs";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
test("native self-check drafts restore selections and keep measurement progress through errors", async () => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-task-drafts-"),
  );
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_DESKTOP_LIVE: "0",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  let app;
  try {
    app = await electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env,
    });
    const page = await app.firstWindow();
    await page.waitForURL(/http:\/\/127\.0\.0\.1:\d+\//);
    await page.getByText("本机服务已连接").waitFor({ timeout: 15000 });
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].setSize(900, 680),
    );
    await verifyLocationDraft(page, {
      shot: path.join(root, "docs/assets/ui/73-native-location-draft.png"),
    });
    await page.reload();
    await page.getByText("本机服务已连接").waitFor({ timeout: 15000 });
    await verifyTemperatureProgress(page, {
      shot: path.join(
        root,
        "docs/assets/ui/73-native-temperature-progress.png",
      ),
    });
  } finally {
    await app?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
