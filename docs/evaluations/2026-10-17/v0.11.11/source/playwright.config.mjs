import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/ui",
  outputDir: "test-results/ui",
  timeout: 30000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:18766",
    headless: true,
    viewport: { width: 1440, height: 1080 },
    launchOptions: {
      executablePath: process.env.CHROME_BIN || "/usr/bin/google-chrome",
    },
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "PYTHONPATH=src python3 -m medagent.server --backend demo --port 18766",
    url: "http://127.0.0.1:18766/api/health",
    reuseExistingServer: false,
  },
});
