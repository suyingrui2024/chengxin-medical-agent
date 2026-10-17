import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createAssertionModel,
  verifyAssertionFlow,
} from "../fixtures/assertion-flow.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("native controller rejects false denials and keeps unknown history through summary corrections", async () => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-assertion-"),
  );
  const model = await createAssertionModel();
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "compatible",
    MEDAGENT_LLM_BASE_URL: model.endpoint,
    MEDAGENT_LLM_MODEL: "fixture",
    MEDAGENT_LLM_API_KEY: "local-assertion-fixture",
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
    await verifyAssertionFlow(await app.firstWindow(), model);
  } finally {
    await app?.close();
    await model.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
