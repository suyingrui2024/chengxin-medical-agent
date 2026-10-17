import { _electron as electron, test } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { exerciseUncertainReports } from "../fixtures/uncertainty-flow.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("native patient summary preserves unresolved reports and correction history", async () => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-uncertainty-"),
  );
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_DESKTOP_LIVE: "0",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  let desktop;
  try {
    desktop = await electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env,
    });
    await exerciseUncertainReports(await desktop.firstWindow());
  } finally {
    if (desktop) await desktop.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
