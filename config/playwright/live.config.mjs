import { defineConfig } from "@playwright/test";
import { fromRoot } from "./paths.mjs";

export default defineConfig({
  testDir: fromRoot("tests/live"),
  outputDir: fromRoot("test-results/live"),
  timeout: 300000,
  workers: 1,
  retries: 0,
  reporter: "list",
  // Live reports are explicitly synthetic. No trace captures process settings.
  use: { trace: "off", screenshot: "off" },
});
