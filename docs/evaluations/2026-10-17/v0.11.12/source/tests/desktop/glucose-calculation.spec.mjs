import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createGlucoseModel,
  verifyGlucoseCalculation,
} from "../fixtures/glucose-calculation.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
for (const height of [760, 680])
  test(`native glucose conversion keeps sources folded and resumes the medical interview at 900x${height}`, async () => {
    test.setTimeout(60000);
    const temporary = await mkdtemp(
      path.join(root, ".runtime/native-glucose-"),
    );
    const model = await createGlucoseModel();
    const env = {
      ...process.env,
      MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
      MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
      MEDAGENT_DESKTOP_LIVE: "1",
      MEDAGENT_BACKEND: "compatible",
      MEDAGENT_LLM_BASE_URL: model.endpoint,
      MEDAGENT_LLM_MODEL: "fixture",
      MEDAGENT_LLM_API_KEY: "glucose-native-only",
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
      await app.evaluate(
        ({ BrowserWindow }, height) =>
          BrowserWindow.getAllWindows()[0].setSize(900, height),
        height,
      );
      await verifyGlucoseCalculation(await app.firstWindow(), model, {
        shots: path.join(root, "docs/assets/ui"),
        native: true,
        nativeHeight: height,
      });
    } finally {
      await app?.close();
      await model.close();
      await rm(temporary, { recursive: true, force: true });
    }
  });
