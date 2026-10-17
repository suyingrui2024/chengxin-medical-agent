import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createPresentProvenanceModel,
  verifyPresentProvenance,
} from "../fixtures/present-provenance.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
test("native known reports retain scoped sources and replace them after correction", async () => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-present-provenance-"),
  );
  const model = await createPresentProvenanceModel();
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "compatible",
    MEDAGENT_LLM_BASE_URL: model.endpoint,
    MEDAGENT_LLM_MODEL: "fixture",
    MEDAGENT_LLM_API_KEY: "local-provenance",
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
    await verifyPresentProvenance(await app.firstWindow(), model);
  } finally {
    await app?.close();
    await model.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
