import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
function reply(response, index, tool, text = "任务完成，已验证产物。") {
  const events = [
    {
      type: "message_start",
      message: {
        id: `execution-${index}`,
        type: "message",
        role: "assistant",
        model: "deepseek-flash",
        content: [],
        usage: { input_tokens: 20, output_tokens: 1 },
      },
    },
  ];
  events.push(
    {
      type: "content_block_start",
      index: 0,
      content_block: tool
        ? { type: "tool_use", id: `call-${index}`, name: tool.name, input: {} }
        : { type: "text", text: "" },
    },
    {
      type: "content_block_delta",
      index: 0,
      delta: tool
        ? { type: "input_json_delta", partial_json: JSON.stringify(tool.input) }
        : { type: "text_delta", text },
    },
    { type: "content_block_stop", index: 0 },
    {
      type: "message_delta",
      delta: { stop_reason: tool ? "tool_use" : "end_turn" },
      usage: { output_tokens: 20 },
    },
    { type: "message_stop" },
  );
  response.writeHead(200, { "Content-Type": "text/event-stream" });
  response.end(
    events
      .map(
        (event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
      )
      .join(""),
  );
}

test("native execution creates files, shows real subagents, cancels a process and resumes the same workspace", async () => {
  test.setTimeout(60000);
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-execution-"),
  );
  let parent = 0,
    child = 0,
    backgroundChild = 0,
    serial = 0;
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    const isChild = !JSON.stringify(body.messages).includes("NATIVE_EXECUTION");
    let tool = null;
    if (isChild) {
      if (JSON.stringify(body.messages).includes("BACKGROUND_CHILD_VERIFY")) {
        const stage = backgroundChild++;
        if (stage === 0)
          tool = {
            name: "bash",
            input: {
              command: "printf started > child-running.txt; sleep 2",
              description: "Start independent background verification",
            },
          };
        if (stage === 1)
          tool = {
            name: "bash",
            input: {
              command: "printf complete > child-complete.txt",
              description: "Complete independent background verification",
            },
          };
      } else if (child++ === 0)
        tool = {
          name: "bash",
          input: {
            command:
              "test $(cat result.txt) = 1250 && printf verified > review.txt",
            description: "Independently verify numeric result and save review",
          },
        };
    } else {
      const step = parent++;
      if (step === 0)
        tool = {
          name: "bash",
          input: {
            command:
              "printf 'print(1250)\\n' > calc.py && python3 calc.py > result.txt",
            description: "Write Python calculation and save actual output",
          },
        };
      if (step === 1)
        tool = {
          name: "subagent",
          input: {
            description: "Verify calculation output independently",
            prompt:
              "Read result.txt, verify 1250, and save review.txt containing verified.",
            run_in_background: false,
          },
        };
      if (step === 3)
        tool = {
          name: "bash",
          input: {
            command:
              "printf running > running.txt; sleep 90; printf unexpected > should-not-exist.txt",
            description: "Run a cancellable long process for verification",
          },
        };
      if (step === 4)
        tool = {
          name: "bash",
          input: {
            command:
              "test -f calc.py && test ! -f should-not-exist.txt && printf resumed > resumed.txt",
            description:
              "Resume workspace after cancellation and check artifacts",
          },
        };
      if (step === 6)
        tool = {
          name: "bash",
          input: {
            command:
              "printf active > background.txt; sleep 90; printf unexpected > background-unexpected.txt",
            description: "Start background process for lifecycle verification",
            run_in_background: true,
          },
        };
      if (step === 8)
        tool = {
          name: "subagent",
          input: {
            description: "Run background verification after root returns",
            prompt:
              "BACKGROUND_CHILD_VERIFY: run a short verification, then save child-complete.txt.",
            run_in_background: true,
          },
        };
    }
    reply(res, ++serial, tool);
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
    MEDAGENT_LLM_API_KEY: "native-execution-fixture-only",
    MEDAGENT_LLM_MODEL: "deepseek-flash",
  };
  delete environment.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  let app;
  try {
    app = await electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env: environment,
    });
    const page = await app.firstWindow();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "把任务，真正做出来。" }),
    ).toBeVisible();
    await page
      .getByLabel("描述执行任务")
      .fill("NATIVE_EXECUTION：编写和执行脚本，委托子智能体独立复核。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 25000,
    });
    await expect(page.locator(".execution-child")).toHaveCount(2);
    await page
      .locator(".execution-file")
      .filter({ hasText: "result.txt" })
      .click();
    await expect(page.locator("#execution-preview")).toHaveText("1250\n");
    const directory = await page.locator("#execution-directory").textContent();
    expect(await readFile(path.join(directory, "review.txt"), "utf8")).toBe(
      "verified",
    );
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/24-execution-workspace.png"),
    });
    await page.getByLabel("描述执行任务").fill("启动可取消的长任务。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(
      page.locator(".execution-file").filter({ hasText: "running.txt" }),
    ).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: "停止执行", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("已停止", {
      timeout: 12000,
    });
    await page
      .getByLabel("描述执行任务")
      .fill("接续工作，检查取消后没有产生额外文件。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 15000,
    });
    await expect(
      page.locator(".execution-file").filter({ hasText: "resumed.txt" }),
    ).toBeVisible();
    await page
      .getByLabel("描述执行任务")
      .fill("启动后台进程并结束当前模型回合。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText(
      "后台任务仍在运行",
      { timeout: 10000 },
    );
    await page.getByRole("button", { name: "停止执行", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("已停止", {
      timeout: 12000,
    });
    await expect(page.locator("#execution-run")).toBeEnabled();
    await page
      .getByLabel("描述执行任务")
      .fill("启动后台子智能体，完成后保存文件。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(
      page.locator(".execution-file").filter({ hasText: "child-complete.txt" }),
    ).toBeVisible({ timeout: 15000 });
    expect(backgroundChild).toBe(3);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
