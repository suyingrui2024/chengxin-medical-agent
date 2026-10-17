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

test("native delivery checks show failure, repair within budget and restore the real check history", async () => {
  test.setTimeout(60000);
  const temporary = await mkdtemp(path.join(root, ".runtime/native-delivery-"));
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const requests = [];
  const tools = [
    {
      name: "bash",
      input: {
        command: "printf 'assert 2 + 2 == 5\\n' > check.py",
        description: "Create an incorrect test expectation",
      },
    },
    {
      name: "delivery_check",
      input: { label: "新增测试", command: "python3 check.py" },
    },
    null,
    {
      name: "bash",
      input: {
        command: "printf 'assert 2 + 2 == 4\\n' > check.py",
        description: "Correct the expectation using actual arithmetic",
      },
    },
    {
      name: "delivery_check",
      input: { label: "新增测试", command: "python3 check.py" },
    },
    null,
  ];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    requests.push(body);
    const step = requests.length;
    if (step === 4) await gate;
    const tool = tools[step - 1];
    const content = tool
      ? { type: "tool_use", id: `delivery-${step}`, ...tool }
      : {
          type: "text",
          text: step === 3 ? "错误的提前完成" : "修正并复验完成",
        };
    const events = [
      {
        type: "message_start",
        message: {
          id: `delivery-${step}`,
          model: "fixture",
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
        .map(
          (event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
        )
        .join(""),
    );
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const endpoint = `http://127.0.0.1:${server.address().port}/anthropic`;
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "dsh",
    MEDAGENT_LLM_BASE_URL: endpoint,
    MEDAGENT_LLM_API_KEY: "native-delivery-local-only",
    MEDAGENT_LLM_MODEL: "fixture",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  const launch = (environment) =>
    electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env: environment,
    });
  let desktop;
  try {
    desktop = await launch(env);
    let page = await desktop.firstWindow();
    await expect(page.getByText("本机服务已连接")).toBeVisible();
    const settings = await page.evaluate(async () =>
      (await fetch("/api/settings")).json(),
    );
    expect(settings.base_url).toBe(endpoint);
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await page
      .getByLabel("描述执行任务")
      .fill("创建并实际检查错误预期，按工具结果修正并复验。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect.poll(() => requests.length).toBe(4);
    await expect(page.locator("#execution-model")).toContainText("4次模型调用");
    await expect(page.locator("#execution-status")).toHaveText("正在执行");
    await expect(page.locator("#execution-results")).toContainText(
      "正在修正交付检查",
    );
    await expect(page.locator(".execution-final")).toHaveCount(0);
    let chip = page.locator("#execution-check-summary .execution-check-chip");
    await expect(chip).toHaveCount(1);
    await expect(chip).toContainText("退出 1");
    await chip.focus();
    for (let index = 0; index < 2; index++) {
      const response = await page.waitForResponse((response) =>
        /\/api\/execution\/[^/?]+\?after=/.test(response.url()),
      );
      await response.finished();
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve)),
          ),
      );
      await expect(chip).toBeFocused();
    }
    await page.screenshot({
      path: "docs/assets/ui/43-delivery-check-repair.png",
      animations: "disabled",
    });
    release();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成");
    await expect(chip).toContainText("退出 0");
    await expect(chip).toHaveClass(/check-succeeded/);
    await expect(page.locator("#execution-outcome")).toContainText(
      "检查已复验",
    );
    await expect(page.locator(".execution-final")).toHaveCount(1);
    expect(requests).toHaveLength(6);
    expect(JSON.stringify(requests[2])).toContain("AssertionError");
    expect(JSON.stringify(requests[3])).toContain("交付检查纠错");
    await chip.click();
    await expect(
      page.locator(".execution-delivery details[open]").last(),
    ).toContainText("新增测试");
    const identity = path.basename(
      await page.locator("#execution-directory").textContent(),
    );
    await page
      .locator("#execution-files .execution-file")
      .filter({ hasText: "check.py" })
      .click();
    await expect(page.locator("#execution-preview")).toContainText(
      "assert 2 + 2 == 4",
    );
    await page
      .locator(".execution-delivery details[open]")
      .last()
      .locator("summary")
      .click();
    await page.locator("#execution-results").evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    await page.screenshot({
      path: "docs/assets/ui/44-delivery-check-result.png",
      animations: "disabled",
    });
    await expect(page.locator("#execution-close")).toBeEnabled();
    await page.locator("#execution-close").click();
    await expect(page.locator("#execution-check-summary")).toBeHidden();
    await desktop.close();
    desktop = null;
    const offline = { ...env, MEDAGENT_DESKTOP_LIVE: "0" };
    for (const key of [
      "MEDAGENT_LLM_BASE_URL",
      "MEDAGENT_LLM_API_KEY",
      "MEDAGENT_LLM_MODEL",
    ])
      delete offline[key];
    desktop = await launch(offline);
    page = await desktop.firstWindow();
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await page.getByLabel("选择执行工作区").selectOption(identity);
    chip = page.locator("#execution-check-summary .execution-check-chip");
    await expect(chip).toContainText("退出 0");
    await expect(chip).toContainText("历史");
    await expect(chip).toHaveClass(/check-unresolved/);
    await expect(chip).toHaveAttribute("title", /当前文件版本未重新核对/);
    await chip.click();
    await expect(
      page.locator(".execution-delivery details[open]").last(),
    ).toContainText("已保存的运行观察");
    await expect(page.locator(".execution-delivery")).toHaveCount(2);
    expect(requests).toHaveLength(6);
  } finally {
    release();
    await desktop?.close();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
