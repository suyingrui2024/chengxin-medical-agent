import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
function respond(response, step, tool) {
  const content = tool
    ? { type: "tool_use", id: `review-${step}`, ...tool }
    : { type: "text", text: "已执行并核对文件。" };
  const events = [
    {
      type: "message_start",
      message: {
        id: `review-${step}`,
        type: "message",
        role: "assistant",
        model: "deepseek-flash",
        content: [],
        usage: { input_tokens: 10, output_tokens: 1 },
      },
    },
    { type: "content_block_start", index: 0, content_block: content },
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
}

test("desktop reviews actual file changes and restores ten-minute history offline", async () => {
  test.setTimeout(60000);
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-change-review-"),
  );
  let calls = 0;
  const issues = [];
  const server = createServer(async (request, response) => {
    let text = "";
    for await (const chunk of request) text += chunk;
    const body = JSON.parse(text),
      step = ++calls;
    if (step === 1)
      respond(response, step, {
        name: "bash",
        input: {
          command:
            "printf 'value = 1\\n' > main.py; printf remove > old.txt; printf original > keep.txt",
          description: "Create original files for the next turn",
        },
      });
    else if (step === 3) {
      const seconds = [
        ...JSON.stringify(body).matchAll(/剩余执行时间约 (\d+) 秒/g),
      ].map((m) => Number(m[1]));
      if (!seconds.some((n) => n > 500 && n <= 600))
        issues.push("ten-minute budget not propagated");
      respond(response, step, {
        name: "bash",
        input: {
          command:
            "printf 'value = 2\\n' > main.py; printf created > new.txt; rm old.txt; mkdir -p tests; printf temporary > tests/generated.tmp",
          description:
            "Create real modifications and a temporary test artifact",
        },
      });
    } else if (step === 4)
      respond(response, step, { name: "workspace_changes", input: {} });
    else respond(response, step, null);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const environment = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "dsh",
    MEDAGENT_LLM_BASE_URL: `http://127.0.0.1:${server.address().port}/anthropic`,
    MEDAGENT_LLM_API_KEY: "native-change-fixture-only",
    MEDAGENT_LLM_MODEL: "deepseek-flash",
  };
  delete environment.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  const launch = (env) =>
    electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env,
    });
  let app;
  try {
    app = await launch(environment);
    let page = await app.firstWindow();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await page.getByLabel("描述执行任务").fill("创建原始文件。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 15000,
    });
    await page.getByLabel("执行时限").selectOption("600");
    await page
      .getByLabel("描述执行任务")
      .fill("修改、新建和删除文件，核对本轮变化。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 15000,
    });
    await expect(page.locator("#execution-change-badges")).toContainText(
      "2 新增",
    );
    await page.getByRole("button", { name: /查看本轮变更/ }).click();
    const dialog = page.getByRole("dialog", { name: "本轮文件变更" });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(".change-file")).toHaveCount(4);
    await dialog.locator(".change-file").filter({ hasText: "main.py" }).click();
    await expect(page.locator("#execution-change-diff")).toContainText(
      "-value = 1",
    );
    await expect(page.locator("#execution-change-diff")).toContainText(
      "+value = 2",
    );
    await expect(page.locator(".change-diff-added")).toHaveCount(1);
    await expect(page.locator(".change-diff-deleted")).toHaveCount(1);
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/35-workspace-change-review.png"),
    });
    await dialog.locator(".change-file").filter({ hasText: "old.txt" }).click();
    await expect(page.locator("#execution-change-diff")).toContainText(
      "-remove",
    );
    expect(calls).toBe(5);
    expect(issues).toEqual([]);
    expect(errors).toEqual([]);
    const identity = path.basename(
      await page.locator("#execution-directory").textContent(),
    );
    let release, requested, finished;
    const held = new Promise((resolve) => {
      release = resolve;
    });
    const arrived = new Promise((resolve) => {
      requested = resolve;
    });
    const settled = new Promise((resolve) => {
      finished = resolve;
    });
    const pattern = /\/api\/execution\/[^/]+\/changes\?path=main\.py$/;
    await page.route(pattern, async (route) => {
      requested();
      await held;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          turn_id: "stale-turn",
          entries: [],
          detail: { available: true, diff: "STALE_DIFF" },
        }),
      });
      finished();
    });
    await dialog.locator(".change-file").filter({ hasText: "main.py" }).click();
    await arrived;
    await page
      .getByRole("button", { name: "关闭变更审阅", exact: true })
      .click();
    await page
      .getByRole("button", { name: "＋ 新工作区", exact: true })
      .click();
    release();
    await settled;
    await page.unroute(pattern);
    await expect(page.locator("#execution-change-diff")).not.toContainText(
      "STALE_DIFF",
    );
    await expect(page.locator("#execution-change-dialog")).not.toBeVisible();
    await app.close();
    app = null;
    const offline = { ...environment, MEDAGENT_DESKTOP_LIVE: "0" };
    for (const key of [
      "MEDAGENT_LLM_BASE_URL",
      "MEDAGENT_LLM_MODEL",
      "MEDAGENT_LLM_API_KEY",
    ])
      delete offline[key];
    app = await launch(offline);
    page = await app.firstWindow();
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await page.getByLabel("选择执行工作区").selectOption(identity);
    await page.getByRole("button", { name: /查看本轮变更/ }).click();
    await page.locator(".change-file").filter({ hasText: "main.py" }).click();
    await expect(page.locator("#execution-change-diff")).toContainText(
      "-value = 1",
    );
    await expect(page.locator("#execution-change-diff")).toContainText(
      "+value = 2",
    );
    await expect(page.locator("#execution-run")).toBeDisabled();
    expect(calls).toBe(5);
  } finally {
    await app?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
