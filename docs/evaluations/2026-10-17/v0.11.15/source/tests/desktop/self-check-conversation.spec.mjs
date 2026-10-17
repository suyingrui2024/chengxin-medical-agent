import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createSelfCheckConversationModel,
  verifySelfCheckConversation,
} from "../fixtures/self-check-conversation.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
test("native self-check conversation retains drafts and confirms original location evidence", async () => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-task-conversation-"),
  );
  const model = await createSelfCheckConversationModel();
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "compatible",
    MEDAGENT_LLM_BASE_URL: model.endpoint,
    MEDAGENT_LLM_MODEL: "fixture",
    MEDAGENT_LLM_API_KEY: "local-task-conversation",
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
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].setSize(900, 680),
    );
    await verifySelfCheckConversation(await app.firstWindow(), model, {
      shot: "docs/assets/ui/75-native-self-check-conversation.png",
    });
  } finally {
    await app?.close();
    await model.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
