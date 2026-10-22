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

export default defineConfig({
  testDir: "./tests/ui",
  outputDir: "test-results/ui",
  timeout: 30000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:18766",
    headless: true,
    viewport: { width: 1440, height: 1080 },
    launchOptions: browserExecutable
      ? { executablePath: browserExecutable }
      : undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "MEDAGENT_PATIENT_DATA_DIR=test-results/ui/patient-data PYTHONPATH=src python3 -m medagent.server --backend demo --port 18766",
    url: "http://127.0.0.1:18766/api/health",
    reuseExistingServer: false,
  },
});
