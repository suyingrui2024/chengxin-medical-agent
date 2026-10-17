import { _electron as electron, test, expect } from "@playwright/test";
import { mkdtemp, writeFile, readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("closing the startup window stops a delayed sidecar without a blocking error dialog", async () => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/desktop-startup-close-"),
  );
  const marker = path.join(temporary, "started.txt");
  const python = path.join(temporary, "delayed-python");
  await writeFile(
    python,
    `#!/usr/bin/python3\nimport os,time,sys\nfrom pathlib import Path\nPath(${JSON.stringify(marker)}).write_text(str(os.getpid()))\ntime.sleep(4)\nos.execv('/usr/bin/python3',['/usr/bin/python3',*sys.argv[1:]])\n`,
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
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  let desktop;
  try {
    desktop = await electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env,
    });
    const page = await desktop.firstWindow();
    await expect
      .poll(async () =>
        readFile(marker, "utf8")
          .then(Boolean)
          .catch(() => false),
      )
      .toBe(true);
    expect(page.url()).toContain("starting.html");
    const started = Date.now();
    await desktop.close();
    desktop = null;
    expect(Date.now() - started).toBeLessThan(8000);
    const pid = Number(await readFile(marker, "utf8"));
    await expect
      .poll(() => {
        try {
          process.kill(pid, 0);
          return true;
        } catch {
          return false;
        }
      })
      .toBe(false);
  } finally {
    await desktop?.close();
    await rm(temporary, { recursive: true, force: true });
  }
});
