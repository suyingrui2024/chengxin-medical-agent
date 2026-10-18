import { _electron as electron, test, expect } from "@playwright/test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifyNativePatientZoom } from "../fixtures/patient-reflow.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
test("native page zoom from 125 to 200 percent keeps all temperature actions reachable", async ({}, info) => {
  test.setTimeout(60000);
  const profile = await mkdtemp(path.join(root, ".runtime/patient-zoom-"));
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: profile,
    MEDAGENT_BACKEND: "demo",
    MEDAGENT_DESKTOP_LIVE: "0",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const app = await electron.launch({ args: [root], env });
  try {
    const page = await app.firstWindow();
    await expect(page.getByText("本机服务已连接")).toBeVisible({
      timeout: 15000,
    });
    const setZoom = (factor) =>
      app.evaluate(({ BrowserWindow }, factor) => {
        const window = BrowserWindow.getAllWindows()[0];
        window.setSize(900, 680);
        window.webContents.setZoomFactor(factor);
        return window.webContents.getZoomFactor();
      }, factor);
    const receipt = await verifyNativePatientZoom(page, setZoom, {
      shot: async (factor) => {
        const png = await app.evaluate(async ({ BrowserWindow }) =>
          (await BrowserWindow.getAllWindows()[0].capturePage())
            .toPNG()
            .toString("base64"),
        );
        await writeFile(
          info.outputPath(`native-temperature-${Math.round(factor * 100)}.png`),
          Buffer.from(png, "base64"),
        );
      },
    });
    await writeFile(
      info.outputPath("native-zoom.json"),
      JSON.stringify(receipt, null, 2) + "\n",
    );
  } finally {
    await app.close();
    await rm(profile, { recursive: true, force: true });
  }
});
