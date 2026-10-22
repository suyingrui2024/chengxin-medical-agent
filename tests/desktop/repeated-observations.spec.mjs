import { _electron as electron, test, expect } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createRepeatedObservationModel,
  verifyRepeatedObservations,
  verifyResumedObservationQuestion,
} from "../fixtures/repeated-observations.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
for (const resumeHeight of [null, 760, 680])
  test(
    resumeHeight
      ? `native repeat resumes the original question with all options visible at 900x${resumeHeight}`
      : "native repeat observations preserve history, resume and reassess risk",
    async () => {
      const temporary = await mkdtemp(
        path.join(root, ".runtime/native-repeated-observations-"),
      );
      const model = await createRepeatedObservationModel();
      const env = {
        ...process.env,
        MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
        MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
        MEDAGENT_DESKTOP_LIVE: "1",
        MEDAGENT_BACKEND: "compatible",
        MEDAGENT_LLM_BASE_URL: model.endpoint,
        MEDAGENT_LLM_MODEL: "fixture",
        MEDAGENT_LLM_API_KEY: "local-observations",
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
        await app.firstWindow();
        if (resumeHeight) {
          const size = await app.evaluate(({ BrowserWindow }, height) => {
            const window = BrowserWindow.getAllWindows()[0];
            window.setSize(900, height);
            return window.getSize();
          }, resumeHeight);
          expect(size).toEqual([900, resumeHeight]);
          await verifyResumedObservationQuestion(
            await app.firstWindow(),
            model,
            resumeHeight === 680
              ? "docs/assets/ui/61-native-small-observation.png"
              : undefined,
          );
        } else await verifyRepeatedObservations(await app.firstWindow(), model);
      } finally {
        await app?.close();
        await model.close();
        await rm(temporary, { recursive: true, force: true });
      }
    },
  );
