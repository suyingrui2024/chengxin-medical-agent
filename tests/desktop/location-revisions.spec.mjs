import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createLocationAnswersModel } from "../fixtures/location-answers.mjs";
import { verifyLocationRevision } from "../fixtures/location-revisions.mjs";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
test("native confirmed location revisions cancel safely and resume the original question", async () => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-location-revisions-"),
  );
  const model = await createLocationAnswersModel();
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "compatible",
    MEDAGENT_LLM_BASE_URL: model.endpoint,
    MEDAGENT_LLM_MODEL: "fixture",
    MEDAGENT_LLM_API_KEY: "local-location-answers",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  let app;
  try {
    const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
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
    await verifyLocationRevision(page, model, {
      shot: "docs/assets/ui/78-native-location-revision.png",
    });
  } finally {
    await app?.close();
    await model.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
