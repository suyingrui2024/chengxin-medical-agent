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
  const events = [
    {
      type: "message_start",
      message: {
        id: `clarify-${step}`,
        type: "message",
        role: "assistant",
        model: "deepseek-flash",
        content: [],
        usage: { input_tokens: 10, output_tokens: 1 },
      },
    },
    {
      type: "content_block_start",
      index: 0,
      content_block: tool
        ? { type: "tool_use", id: `clarify-${step}`, ...tool }
        : { type: "text", text: "已依据真实回答完成并保留原行为。" },
    },
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

test("native clarification waits for real answers, recovers a lost response and restores history", async () => {
  test.setTimeout(60000);
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-clarification-"),
  );
  let serial = 0,
    parent = 0,
    child = 0;
  const issues = [];
  const server = createServer(async (request, response) => {
    let text = "";
    for await (const chunk of request) text += chunk;
    const body = JSON.parse(text),
      serialized = JSON.stringify(body.messages);
    const isRoot = serialized.includes("ROOT_NATIVE_CLARIFICATION");
    let tool = null;
    if (isRoot) {
      const step = parent++;
      if (step === 0)
        tool = {
          name: "bash",
          input: {
            command: "printf 'warn = True\\n' > rule.py",
            description: "Create a fixture with existing warning behavior",
          },
        };
      else if (step === 1)
        tool = {
          name: "request_clarification",
          input: {
            question: "需求有冲突：要保留现有告警吗？",
            context: "原描述要求不告警，现有测试要求保留告警并调整文案。",
            options: ["保留告警，只改文案", "取消这类告警"],
          },
        };
      else if (step === 2) {
        if (!serialized.includes("保留告警，只改文案"))
          issues.push("root answer absent");
        tool = {
          name: "subagent",
          input: {
            description: "Review selected requirements",
            prompt:
              "Ask the user for a brief review note using request_clarification without options. After the actual reply, write reviewed to review.txt.",
            run_in_background: false,
          },
        };
      } else if (step === 3)
        tool = {
          name: "bash",
          input: {
            command:
              "python3 -c \"from pathlib import Path;assert Path('rule.py').read_text()=='warn = True\\n';Path('result.txt').write_text('preserved')\"",
            description: "Verify original behavior after actual clarification",
          },
        };
    } else {
      const step = child++;
      if (step === 0)
        tool = {
          name: "request_clarification",
          input: { question: "请补充本次复核的备注。" },
        };
      else if (step === 1) {
        if (!serialized.includes("<b>仅记录复核</b>"))
          issues.push("child free-text answer absent");
        tool = {
          name: "bash",
          input: {
            command: "printf reviewed > review.txt",
            description: "Save independent review after the actual user reply",
          },
        };
      }
    }
    respond(response, ++serial, tool);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const environment = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
    MEDAGENT_EXECUTION_ROOT: path.join(temporary, "records"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "dsh",
    MEDAGENT_LLM_BASE_URL: `http://127.0.0.1:${server.address().port}/anthropic`,
    MEDAGENT_LLM_API_KEY: "native-clarification-fixture-only",
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
    const enter = async () => {
      await page.getByLabel("界面模式").selectOption("developer");
      await page
        .getByRole("button", { name: "执行工作台", exact: true })
        .click();
    };
    await enter();
    await page
      .getByLabel("描述执行任务")
      .fill("ROOT_NATIVE_CLARIFICATION 核对需求，再运行与复核。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("需要你核对", {
      timeout: 15000,
    });
    await expect(page.locator("#execution-run")).toBeDisabled();
    await expect(page.locator("#execution-close")).toBeDisabled();
    const active = page.locator(
      ".execution-question:not(.execution-question-history)",
    );
    await expect(active.locator("h3")).toHaveText(
      "需求有冲突：要保留现有告警吗？",
    );
    await expect(
      active.getByRole("button", { name: "保留告警，只改文案", exact: true }),
    ).toBeVisible();
    await expect(active.getByLabel("补充回答")).toBeHidden();
    await page.waitForTimeout(250);
    expect(serial).toBe(2);
    const identity = await page.locator("#execution-workspace").inputValue();
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/37-execution-clarification.png"),
    });
    await page.evaluate(() => {
      const original = window.fetch;
      let lost = false;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (!lost && String(args[0]).endsWith("/answer")) {
          lost = true;
          throw new TypeError(
            "Synthetic response loss after the server committed",
          );
        }
        return response;
      };
    });
    await active
      .getByRole("button", { name: "保留告警，只改文案", exact: true })
      .click();
    await active
      .getByRole("button", { name: "发送回答 ↗", exact: true })
      .click();
    await expect(active.locator("h3")).toHaveText("请补充本次复核的备注。", {
      timeout: 15000,
    });
    await expect(active).toContainText("子任务");
    await active.getByLabel("补充回答").fill("<b>仅记录复核</b>");
    await page.waitForTimeout(850);
    await expect(active.getByLabel("补充回答")).toHaveValue(
      "<b>仅记录复核</b>",
    );
    await active
      .getByRole("button", { name: "发送回答 ↗", exact: true })
      .click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 15000,
    });
    expect(serial).toBe(8);
    expect(child).toBe(3);
    expect(issues).toEqual([]);
    expect(errors).toEqual([]);
    expect(await page.locator("#execution-clarifications b").count()).toBe(0);
    const snapshot = await page.evaluate(
      async (id) => (await fetch(`/api/execution/${id}`)).json(),
      identity,
    );
    expect(snapshot.turns).toHaveLength(1);
    expect(snapshot.clarifications.map((q) => q.status)).toEqual([
      "answered",
      "answered",
    ]);
    const duplicate = await page.evaluate(
      async ({ id, q }) =>
        (
          await fetch(`/api/execution/${id}/answer`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              request_id: q.id,
              turn_id: q.turn_id,
              answer: q.answer,
            }),
          })
        ).status,
      { id: identity, q: snapshot.clarifications[0] },
    );
    expect(duplicate).toBe(200);
    expect(serial).toBe(8);
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
    await enter();
    await page.getByLabel("选择执行工作区").selectOption(identity);
    await expect(page.locator("#execution-clarifications summary")).toHaveCount(
      2,
    );
    await page.locator("#execution-clarifications summary").last().click();
    await expect(page.locator("#execution-clarifications")).toContainText(
      "<b>仅记录复核</b>",
    );
    await expect(page.locator("#execution-run")).toBeDisabled();
    expect(serial).toBe(8);
    await page.getByLabel("关闭当前工作区").click();
    await expect(page.locator("#execution-clarifications")).toBeHidden();
  } finally {
    await app?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
