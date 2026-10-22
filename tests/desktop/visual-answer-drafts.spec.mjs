import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifyDegreeDraft } from "../fixtures/visual-answer-drafts.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
test("native degree selection preserves independent patient text and confirms explicitly at 900x680", async () => {
  const temporary = await mkdtemp(path.join(root, ".runtime/native-degree-"));
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
    await app.firstWindow();
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].setSize(900, 680),
    );
    await verifyDegreeDraft(await app.firstWindow(), {
      shot: path.join(root, "docs/assets/ui/71-native-degree-draft.png"),
    });
  } finally {
    await app?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
