import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/live",
  outputDir: "test-results/live",
  timeout: 300000,
  workers: 1,
  retries: 0,
  reporter: "list",
  // Live reports are explicitly synthetic. No trace captures process settings.
  use: { trace: "off", screenshot: "off" },
});
