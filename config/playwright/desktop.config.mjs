import { defineConfig } from "@playwright/test";
import { fromRoot } from "./paths.mjs";

export default defineConfig({
  testDir: fromRoot("tests/desktop"),
  outputDir: fromRoot("test-results/desktop"),
  timeout: 45000,
  workers: 1,
  use: { trace: "retain-on-failure" },
});
