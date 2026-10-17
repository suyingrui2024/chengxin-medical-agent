import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createPatientCalculationModel,
  verifyPatientCalculation,
} from "../fixtures/patient-calculation.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
test("native patient calculation uses real controller confirmation and resumes the interview", async () => {
  test.setTimeout(60000);
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const temporary = await mkdtemp(
      path.join(root, ".runtime/native-patient-calculation-"),
    ),
    model = await createPatientCalculationModel();
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "compatible",
    MEDAGENT_LLM_BASE_URL: model.endpoint,
    MEDAGENT_LLM_MODEL: "fixture",
    MEDAGENT_LLM_API_KEY: "native-patient-test-only",
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
    await verifyPatientCalculation(await app.firstWindow(), model, {
      shots: path.join(root, "docs/assets/ui"),
    });
  } finally {
    await app?.close();
    await model.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
