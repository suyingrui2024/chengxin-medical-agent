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

test("native checks mark changed inputs as needing revalidation and accept the actual new check", async () => {
  test.setTimeout(60000);
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-freshness-"),
  );
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const requests = [];
  const tools = [
    {
      name: "bash",
      input: {
        command: "printf 'assert 2 + 2 == 4\\n' > check.py",
        description: "创建可运行检查",
      },
    },
    {
      name: "delivery_check",
      input: {
        label: "代码检查",
        command: "python3 check.py",
        watch: ["check.py"],
      },
    },
    {
      name: "bash",
      input: {
        command: "printf '# reviewed change\\n' >> check.py",
        description: "更新检查依赖文件",
      },
    },
    null,
    {
      name: "delivery_check",
      input: {
        label: "代码检查",
        command: "python3 check.py",
        watch: ["check.py"],
      },
    },
    null,
  ];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks).toString()));
    const step = requests.length;
    if (step === 5) await gate;
    const tool = tools[step - 1];
    const block = tool
      ? { type: "tool_use", id: `freshness-${step}`, ...tool }
      : {
          type: "text",
          text: step === 4 ? "提前结束，尚未复验新文件" : "当前文件已实际复验",
        };
    const events = [
      {
        type: "message_start",
        message: {
          id: `freshness-${step}`,
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
        .map(
          (event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
        )
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
    MEDAGENT_LLM_API_KEY: "native-freshness-local-only",
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
      .fill("检查代码，更新文件后重新执行检查。改动后的文件需要新的验证结果。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect.poll(() => requests.length).toBe(5);
    const chip = page.locator("#execution-check-summary .execution-check-chip");
    await expect(chip).toHaveCount(1);
    await expect(chip).toContainText("需复验");
    await expect(chip).toHaveClass(/check-unresolved/);
    await expect(page.locator("#execution-status")).toHaveText("正在执行");
    await expect(page.locator(".execution-final")).toHaveCount(0);
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/45-check-needs-revalidation.png"),
      fullPage: true,
    });
    release();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成");
    await expect(chip).toHaveClass(/check-succeeded/);
    await expect(chip).toContainText("退出 0");
    await expect(chip).toHaveAttribute("title", /check.py/);
    expect(requests.length).toBe(6);
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/46-check-revalidated.png"),
      fullPage: true,
    });
  } finally {
    release();
    if (desktop) await desktop.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
