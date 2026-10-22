import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test.skip(
  process.platform !== "linux",
  "full DSH execution requires Linux bubblewrap",
);

test("native rejected check inputs remain visible while corrected actual checks complete", async () => {
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-check-inputs-"),
  );
  const requests = [];
  const calls = [
    {
      name: "bash",
      input: {
        command: "printf 'assert 2 + 2 == 4\\n' > check.py",
        description: "创建检查",
      },
    },
    {
      name: "delivery_check",
      input: {
        label: "参数负例",
        command: "python3 check.py | tail -1",
        timeout_ms: 1800001,
        watch: ["check.py"],
      },
    },
    {
      name: "delivery_check",
      input: {
        label: "实际检查",
        command: "python3 check.py",
        timeout_ms: 240000,
        watch: ["check.py"],
      },
    },
    null,
  ];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks).toString()));
    const step = requests.length,
      tool = calls[step - 1];
    const block = tool
      ? { type: "tool_use", id: `check-input-${step}`, ...tool }
      : { type: "text", text: "参数负例未执行；已改正并实际检查通过。" };
    const events = [
      {
        type: "message_start",
        message: {
          id: `check-input-${step}`,
          model: "fixture",
          usage: { input_tokens: 10, output_tokens: 1 },
        },
      },
      { type: "content_block_start", index: 0, content_block: block },
      { type: "content_block_stop", index: 0 },
      {
        type: "message_delta",
        delta: { stop_reason: tool ? "tool_use" : "end_turn" },
        usage: { output_tokens: 10 },
      },
      { type: "message_stop" },
    ];
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    response.end(
      events
        .map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`)
        .join(""),
    );
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "dsh",
    MEDAGENT_LLM_BASE_URL: `http://127.0.0.1:${server.address().port}/anthropic`,
    MEDAGENT_LLM_API_KEY: "native-check-inputs-local-only",
    MEDAGENT_LLM_MODEL: "fixture",
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
    await expect(page.getByText("本机服务已连接")).toBeVisible();
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await page
      .getByLabel("描述执行任务")
      .fill("验证参数拒绝后可以改正，并完成实际检查。保留第一次错误记录。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成");
    expect(requests.length).toBe(4);
    const chip = page.locator("#execution-check-summary .execution-check-chip");
    await expect(chip).toHaveCount(1);
    await expect(chip).toHaveClass(/check-succeeded/);
    await expect(chip).toContainText("实际检查");
    await expect(chip).toContainText("退出 0");
    await expect(page.locator(".execution-delivery")).toHaveCount(2);
    await expect(page.locator(".execution-final")).toContainText(
      "参数负例未执行",
    );
    await page.locator("#execution-outcome").click();
    await expect(page.locator("#execution-results")).toContainText("参数无效");
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/47-rejected-input-recovery.png"),
      animations: "disabled",
    });
  } finally {
    await desktop?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
