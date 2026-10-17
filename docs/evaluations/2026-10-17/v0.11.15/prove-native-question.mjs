import { _electron as electron, expect } from "@playwright/test";
import { mkdtemp, mkdir, rm, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createLocationAnswersModel, verifyLocationAnswers } from "./actual-question-replay.mjs";

const work = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(work, "../..");
const out = path.join(work, "native-proof");
await mkdir(out, {recursive: true});
const temporary = await mkdtemp(path.join(work, "proof-profile-"));
const model = await createLocationAnswersModel(false);
const binary = path.join(root, "dist/desktop/Chengxin-0.11.15-linux-x64/Chengxin");
const env = {...process.env,
  MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
  MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
  MEDAGENT_DESKTOP_LIVE: "1", MEDAGENT_BACKEND: "compatible",
  MEDAGENT_LLM_BASE_URL: model.endpoint,
  MEDAGENT_LLM_MODEL: "fixture", MEDAGENT_LLM_API_KEY: "native-location-proof-only",
};
delete env.ELECTRON_RUN_AS_NODE;
let app;
try {
  app = await electron.launch({executablePath: binary, args: [], env});
  await app.evaluate(({BrowserWindow}) => BrowserWindow.getAllWindows()[0].setSize(900,680));
  const page = await app.firstWindow();
  await verifyLocationAnswers(page, model, {shotPrefix:path.join(out, "actual-question")});
  const measurements = await page.evaluate(() => window.locationAnswerGeometry);
  expect(measurements.map(x => x.stage)).toEqual(["area", "extent", "review"]);
  const source = JSON.parse(await readFile(path.join(work,"actual-question-source.json"),"utf8"));
  await writeFile(path.join(out,"measurements.json"), JSON.stringify({
    version:"0.11.15", binary, native_window:[900,680], source,
    measurements, local_fixture_requests:model.requests.length,
    external_provider_requests:0, passed:true,
    scope:"Actual final binary with locally replayed real model question and matching declared fields; scripted remaining setup and follow-up. Not a second real-provider consultation.",
  },null,2)+"\n");
  process.stdout.write(JSON.stringify({passed:true,stages:measurements.length,local_requests:model.requests.length,external_provider_requests:0})+"\n");
} finally {
  await app?.close();
  await model.close();
  await rm(temporary,{recursive:true,force:true});
}
