import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const browserExecutable = [
  process.env.CHROME_BIN,
  process.platform === "darwin"
    ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    : null,
  process.platform === "win32"
    ? `${process.env.PROGRAMFILES || "C:\\Program Files"}\\Google\\Chrome\\Application\\chrome.exe`
    : null,
  process.platform === "win32" && process.env["PROGRAMFILES(X86)"]
    ? `${process.env["PROGRAMFILES(X86)"]}\\Google\\Chrome\\Application\\chrome.exe`
    : null,
  process.platform === "linux" ? "/usr/bin/google-chrome" : null,
  process.platform === "linux" ? "/usr/bin/chromium" : null,
].find((candidate) => candidate && existsSync(candidate));

/**
 * The port and the output directory are parameters so the suite can be
 * sharded. Each shard needs its own: the browser suite starts a fresh server
 * per spec file and wipes the output directory between files, so two shards
 * sharing either would spend their time fighting instead of testing.
 *
 * The defaults are the single-shard behaviour this configuration has always
 * had, so a plain `npx playwright test` still binds 18766 and writes to
 * test-results/ui.
 */
const PORT = Number(process.env.UI_PORT || 18766);
const OUTPUT_DIR = process.env.UI_OUTPUT_DIR || "test-results/ui";

export default defineConfig({
  testDir: "./tests/ui",
  outputDir: OUTPUT_DIR,
  timeout: 30000,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    headless: true,
    // One viewport key only: a duplicated literal silently kept whichever
    // came last, so the 1280x900 line above it was dead.
    viewport: { width: 1440, height: 1080 },
    launchOptions: browserExecutable
      ? { executablePath: browserExecutable }
      : undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `MEDAGENT_PATIENT_DATA_DIR=${OUTPUT_DIR}/patient-data PYTHONPATH=src python3 -m medagent.server --backend demo --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
  },
});
