import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/desktop",
  outputDir: "test-results/desktop",
  timeout: 45000,
  workers: 1,
  use: { trace: "retain-on-failure" },
});
