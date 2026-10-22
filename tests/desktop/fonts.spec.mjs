import { _electron as electron, test, expect } from "@playwright/test";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { checkBundledFonts } from "../../scripts/build/check-bundled-fonts.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("native startup and patient UI render Chinese with the packaged fonts", async ({}, testInfo) => {
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const temporary = await mkdtemp(path.join(root, ".runtime/native-fonts-"));
  const python = path.join(temporary, "delayed-python");
  await writeFile(
    python,
    "#!/usr/bin/python3\nimport os,time,sys\ntime.sleep(4)\nos.execv('/usr/bin/python3',['/usr/bin/python3',*sys.argv[1:]])\n",
    { mode: 0o700 },
  );
  const env = {
    ...process.env,
    MEDAGENT_PYTHON: python,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_DESKTOP_LIVE: "0",
    MEDAGENT_BACKEND: "demo",
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
    const page = await app.firstWindow();
    await page.waitForURL(/starting\.html$/);
    const startup = await checkBundledFonts(page);
    await mkdir(path.join(root, "docs/assets/ui"), { recursive: true });
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/79-startup-bundled-fonts.png"),
    });
    await expect(
      page.getByRole("button", { name: "开始问诊", exact: true }),
    ).toBeVisible({ timeout: 15000 });
    const patient = await checkBundledFonts(page);
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/80-patient-bundled-fonts.png"),
    });
    const output = testInfo.outputPath("font-proof.json");
    await mkdir(path.dirname(output), { recursive: true });
    await writeFile(
      output,
      JSON.stringify({ startup, patient, passed: true }, null, 2) + "\n",
    );
  } finally {
    await app?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
