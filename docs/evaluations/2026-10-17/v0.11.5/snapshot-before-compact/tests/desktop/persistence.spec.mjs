import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import { mkdir, mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
function reply(response, step, content) {
  const events = [
    {
      type: "message_start",
      message: {
        id: `persistent-${step}`,
        type: "message",
        role: "assistant",
        model: "deepseek-flash",
        content: [],
        usage: { input_tokens: 20, output_tokens: 1 },
      },
    },
    { type: "content_block_start", index: 0, content_block: content },
    { type: "content_block_stop", index: 0 },
    {
      type: "message_delta",
      delta: {
        stop_reason: content.type === "tool_use" ? "tool_use" : "end_turn",
      },
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

test("desktop restores offline history, rotates credentials and resumes the original SDK session", async () => {
  test.setTimeout(60000);
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-persistence-"),
  );
  const marker = "native-amber-sparrow-731";
  let calls = 0;
  const issues = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    const step = ++calls;
    if (!JSON.stringify(body.messages).includes(marker))
      issues.push("original conversation missing");
    const expectedKey =
      step <= 2 ? "native-persist-first-key" : "native-persist-rotated-key";
    if (request.headers["x-api-key"] !== expectedKey)
      issues.push("credential route incorrect");
    const content =
      step === 1 || step === 3
        ? {
            type: "tool_use",
            id: `persist-tool-${step}`,
            name: "bash",
            input: {
              command:
                step === 1
                  ? `printf '%s' '${marker}' > original.txt`
                  : `test $(cat original.txt) = ${marker} && printf resumed > followup.txt`,
              description: "Use actual original workspace",
            },
          }
        : {
            type: "text",
            text:
              step === 2
                ? "初始任务已执行，原始文件已核对。"
                : "已在原会话接续，followup.txt 已保存。",
          };
    reply(response, step, content);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const endpoint = `http://127.0.0.1:${server.address().port}/anthropic`;
  const environment = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "dsh",
    MEDAGENT_LLM_BASE_URL: endpoint,
    MEDAGENT_LLM_MODEL: "deepseek-flash",
    MEDAGENT_LLM_API_KEY: "native-persist-first-key",
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
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await page
      .getByLabel("描述执行任务")
      .fill(`PERSIST_ORIGINAL: ${marker}。请创建并核对 original.txt。`);
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 15000,
    });
    const directory = await page.locator("#execution-directory").textContent();
    const identity = path.basename(directory);
    expect(calls).toBe(2);
    const competingEnvironment = {
      ...environment,
      MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "competing-profile"),
      MEDAGENT_DESKTOP_LIVE: "0",
    };
    for (const key of [
      "MEDAGENT_LLM_BASE_URL",
      "MEDAGENT_LLM_API_KEY",
      "MEDAGENT_LLM_MODEL",
    ])
      delete competingEnvironment[key];
    const competitor = await launch(competingEnvironment);
    try {
      const competingPage = await competitor.firstWindow();
      await competingPage.getByLabel("界面模式").selectOption("developer");
      await competingPage
        .getByRole("button", { name: "执行工作台", exact: true })
        .click();
      await competingPage.getByLabel("选择执行工作区").selectOption(identity);
      await expect(competingPage.locator("#execution-feedback")).toContainText(
        "另一个窗口",
      );
      expect(calls).toBe(2);
    } finally {
      await competitor.close();
    }
    await app.close();
    app = null;

    const offline = { ...environment, MEDAGENT_DESKTOP_LIVE: "0" };
    for (const key of [
      "MEDAGENT_LLM_BASE_URL",
      "MEDAGENT_LLM_API_KEY",
      "MEDAGENT_LLM_MODEL",
    ])
      delete offline[key];
    app = await launch(offline);
    page = await app.firstWindow();
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await page.getByLabel("选择执行工作区").selectOption(identity);
    await expect(page.locator("#execution-directory")).toHaveText(directory);
    await expect(page.getByText("历史执行结果", { exact: true })).toBeVisible();
    await expect(page.locator("#execution-model")).toContainText(
      "仅查看已保存记录",
    );
    await expect(page.locator("#execution-run")).toBeDisabled();
    await page
      .locator(".execution-file")
      .filter({ hasText: "original.txt" })
      .click();
    await expect(page.locator("#execution-preview")).toHaveText(marker);
    expect(calls).toBe(2);
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/28-restored-workspace.png"),
    });

    await page
      .getByRole("button", { name: "模型与运行配置", exact: true })
      .click();
    await page.locator('input[name="backend"][value="dsh"]').check();
    await page.locator("#base-url").fill(endpoint);
    await page.locator("#model-id").fill("deepseek-flash");
    await page.locator("#api-key").fill("native-persist-rotated-key");
    await page.locator("#save-settings").click();
    await expect(page.locator("#settings-dialog")).not.toBeVisible();
    await expect(page.locator("#execution-run")).toBeEnabled();
    expect(calls).toBe(2);
    await page
      .getByLabel("描述执行任务")
      .fill("继续原任务，核对原始记录并写出 followup.txt。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 15000,
    });
    await expect(
      page.locator(".execution-file").filter({ hasText: "followup.txt" }),
    ).toBeVisible();
    expect(await readFile(path.join(directory, "followup.txt"), "utf8")).toBe(
      "resumed",
    );
    expect(calls).toBe(4);
    await expect(page.locator("#workspace-storage-note")).toHaveText(
      "文件与任务记录保存在本机",
    );
    expect(issues).toEqual([]);
    await page
      .getByRole("button", { name: "关闭当前工作区", exact: true })
      .click();
    await expect(page.locator("#execution-status")).toHaveText("工作区已关闭");
    await page.getByLabel("选择执行工作区").selectOption(identity);
    await expect(page.getByText("历史执行结果", { exact: true })).toHaveCount(
      2,
    );
    expect(calls).toBe(4);
    const index = path.join(temporary, "workspaces/.index");
    const records = await readdir(index);
    for (const file of records.filter((name) => name.endsWith(".json"))) {
      const raw = await readFile(path.join(index, file), "utf8");
      expect(raw).not.toContain("native-persist-first-key");
      expect(raw).not.toContain("native-persist-rotated-key");
    }
    await expect(page.locator("#toast")).not.toBeVisible({ timeout: 6000 });
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/29-resumed-workspace.png"),
    });
  } finally {
    await app?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
