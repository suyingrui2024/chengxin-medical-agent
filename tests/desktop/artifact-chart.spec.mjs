import { _electron as electron, test, expect } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifyNativeArtifactChart } from "../fixtures/native-artifact-chart.mjs";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
test("native chart preview reads actual workspace CSV bytes without a model call", async ({}, info) => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-table-chart-"),
  );
  let calls = 0;
  const model = createServer((_req, res) => {
    calls++;
    res.writeHead(500);
    res.end("unexpected model request");
  });
  await new Promise((resolve) => model.listen(0, "127.0.0.1", resolve));
  const executionRoot = path.join(temporary, "workspaces");
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: executionRoot,
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "compatible",
    MEDAGENT_LLM_BASE_URL: `http://127.0.0.1:${model.address().port}/v1`,
    MEDAGENT_LLM_API_KEY: "native-chart-fixture-only",
    MEDAGENT_LLM_MODEL: "fixture",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  let desktop;
  try {
    desktop = await electron.launch({ args: [root], env });
    const page = await desktop.firstWindow();
    await expect(page.getByText("本机服务已连接")).toBeVisible({
      timeout: 15000,
    });
    const receipt = await verifyNativeArtifactChart(page, executionRoot);
    await page.locator("#execution-preview-copy").click();
    await expect(page.locator("#execution-preview-action-status")).toHaveText(
      "已复制读取时的原文",
    );
    expect(
      (await desktop.evaluate(({ clipboard }) => clipboard.readText())).replace(
        /\r\n/g,
        "\n",
      ),
    ).toBe("样本,质量 (g)\nA,1250\nB,2000\nC,750\n");
    expect(
      await page.evaluate(async () => {
        try {
          await navigator.clipboard.readText();
          return "unexpectedly allowed";
        } catch {
          return "denied";
        }
      }),
    ).toBe("denied");
    expect(receipt.agent_turns).toBe(0);
    expect(calls).toBe(0);
    await page
      .locator("#execution-preview")
      .screenshot({ path: info.outputPath("native-chart.png") });
  } finally {
    await desktop?.close();
    await new Promise((resolve) => model.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
