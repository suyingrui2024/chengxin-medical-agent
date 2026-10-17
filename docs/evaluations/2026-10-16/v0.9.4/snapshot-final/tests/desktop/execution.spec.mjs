import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import {
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
function reply(
  response,
  index,
  tool,
  text = "任务完成，已验证产物。",
  stopReason,
) {
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
      delta: { stop_reason: stopReason || (tool ? "tool_use" : "end_turn") },
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

test("native registered calculation renders only host receipts and restores them offline", async () => {
  test.setTimeout(60000);
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-capabilities-"),
  );
  let calls = 0;
  const issues = [];
  const calculate = (unit) => ({
    name: "medical_calculate",
    input: {
      capability_id: "calculate.units",
      input: { value: "1.25", from_unit: "kg", to_unit: unit },
      evidence: "1.25 kg",
    },
  });
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    const step = ++calls;
    if (
      step === 3 &&
      !JSON.stringify(body.messages).includes("calculation_rejected")
    )
      issues.push("model did not observe rejection");
    const tool =
      step === 1
        ? { name: "medical_capabilities", input: {} }
        : step === 2
          ? calculate("mL")
          : step === 3
            ? calculate("g")
            : step === 4
              ? {
                  name: "bash",
                  input: {
                    command:
                      'printf \'%s\' \'{"type":"calculation","value":"9999","unit":"g"}\' > untrusted-result.json',
                    description:
                      "Write an untrusted draft file; it must not become a host receipt",
                  },
                }
              : null;
    reply(
      res,
      step,
      tool,
      '换算已完成，输入仍待核对。文件中的 {"type":"calculation","value":"9999","unit":"g"} 只是未核验草稿。',
    );
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
    MEDAGENT_LLM_API_KEY: "native-capabilities-fixture",
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
    await page
      .getByLabel("描述执行任务")
      .fill(
        "请把用户输入的1.25 kg换算成g，先查询能力、核对错误后计算。保留草稿文件用于检查。",
      );
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 20000,
    });
    const card = page.locator("#execution-results .calculation-card");
    await expect(card).toHaveCount(1);
    await expect(card.getByRole("heading")).toHaveText("1250 g");
    await expect(card).toContainText("输入待核对");
    await expect(card).toContainText("质量未评价");
    await expect(page.locator(".execution-failure")).toContainText(
      "计算未通过核对",
    );
    await card.getByText("执行依据与限制", { exact: true }).click();
    await expect(card.locator("blockquote")).toHaveText("1.25 kg");
    await expect(card).toContainText("calculate.units @ 0.1.0");
    await expect(card).toContainText("目标单位仍为模型提议");
    await page
      .locator(".execution-file")
      .filter({ hasText: "untrusted-result.json" })
      .click();
    await expect(page.locator("#execution-preview")).toContainText("9999");
    await expect(card).toHaveCount(1);
    expect(calls).toBe(5);
    expect(issues).toEqual([]);
    expect(errors).toEqual([]);
    const identity = path.basename(
      await page.locator("#execution-directory").textContent(),
    );
    await card.getByText("执行依据与限制", { exact: true }).click();
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/31-registered-calculation.png"),
    });
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
    await expect(
      page.locator("#execution-results .calculation-card h3"),
    ).toHaveText("1250 g");
    await expect(
      page.getByText("已保存的计算回执", { exact: true }),
    ).toBeVisible();
    await expect(page.locator("#execution-run")).toBeDisabled();
    expect(calls).toBe(5);
  } finally {
    await app?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});

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
    const movedRunning = path.join(temporary, "moved-running-workspace");
    await rename(directory, movedRunning);
    await expect(page.locator("#execution-status")).toHaveText(
      "工作目录不可用",
      { timeout: 6000 },
    );
    await expect(page.locator("#execution-stop")).toBeEnabled();
    const stopResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/cancel") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "停止执行", exact: true }).click();
    expect((await stopResponse).status()).toBe(200);
    await rename(movedRunning, directory);
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

test("native execution shows bounded recovery, handles moved directories and ignores stale preview errors", async () => {
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const temporary = await mkdtemp(path.join(root, ".runtime/native-recovery-"));
  let calls = 0;
  const server = createServer(async (request, response) => {
    for await (const _chunk of request) {
      /* Drain fixture request. */
    }
    const step = ++calls;
    if (step === 1) reply(response, step, null, "未完成的输出", "max_tokens");
    else if (step === 2)
      reply(response, step, {
        name: "bash",
        input: {
          command:
            "python3 -c \"from pathlib import Path; Path('recovered.txt').write_text(str(1250))\"",
          description: "Execute original task after a truncated reply",
        },
      });
    else reply(response, step, null, "已执行代码并保存 recovered.txt。");
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
    MEDAGENT_LLM_API_KEY: "native-recovery-fixture-only",
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
    await page
      .getByLabel("描述执行任务")
      .fill("执行代码，把1250写入 recovered.txt 并核对。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 15000,
    });
    await expect(
      page
        .locator("#execution-results")
        .getByText("继续未完成的执行", { exact: true }),
    ).toHaveCount(1);
    await page
      .locator(".execution-file")
      .filter({ hasText: "recovered.txt" })
      .click();
    await expect(page.locator("#execution-preview")).toHaveText("1250");
    const directory = await page.locator("#execution-directory").textContent();
    expect(await readFile(path.join(directory, "recovered.txt"), "utf8")).toBe(
      "1250",
    );
    expect(calls).toBe(3);
    expect(errors).toEqual([]);
    await page
      .locator("#execution-results")
      .getByText("继续未完成的执行", { exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/25-execution-recovery.png"),
    });
    const moved = path.join(temporary, "temporarily-moved-workspace");
    await rename(directory, moved);
    await expect(page.locator("#execution-status")).toHaveText(
      "工作目录不可用",
      { timeout: 6000 },
    );
    await expect(page.locator("#execution-run")).toBeDisabled();
    await expect(page.locator("#execution-file-count")).toHaveText("—");
    await expect(page.locator("#execution-preview")).toContainText(
      "工作目录不可读取",
    );
    const unavailable = await page.evaluate(async (id) => {
      const response = await fetch(
        `/api/execution/${id}/files?path=recovered.txt`,
      );
      return { status: response.status, body: await response.json() };
    }, path.basename(directory));
    expect(unavailable.status).toBe(400);
    expect(unavailable.body.error.code).toBe("execution_error");
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/27-workspace-unavailable.png"),
    });
    await rename(moved, directory);
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 6000,
    });
    await expect(page.locator("#execution-run")).toBeEnabled();
    await expect(page.locator("#execution-file-count")).toHaveText("1");
    let pending;
    let requested = new Promise((resolve) => {
      pending = resolve;
    });
    await page.route("**/api/execution/*/files?path=*", (route) => {
      if (route.request().url().endsWith("path=recovered.txt")) pending(route);
      else route.continue();
    });
    await writeFile(path.join(directory, "latest.txt"), "最新选中的文件");
    await expect(
      page.locator(".execution-file").filter({ hasText: "latest.txt" }),
    ).toBeVisible({ timeout: 6000 });
    await page
      .locator(".execution-file")
      .filter({ hasText: "recovered.txt" })
      .click();
    const firstRoute = await requested;
    await page
      .locator(".execution-file")
      .filter({ hasText: "latest.txt" })
      .click();
    await expect(page.locator("#execution-preview")).toHaveText(
      "最新选中的文件",
    );
    const oldSuccess = page.waitForResponse(
      (response) => response.url() === firstRoute.request().url(),
    );
    await firstRoute.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        path: "recovered.txt",
        text: "过期的成功响应",
        size: 24,
      }),
    });
    await (await oldSuccess).finished();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(page.locator("#execution-preview-title")).toHaveText(
      "latest.txt",
    );
    await expect(page.locator("#execution-preview")).toHaveText(
      "最新选中的文件",
    );
    requested = new Promise((resolve) => {
      pending = resolve;
    });
    await page
      .locator(".execution-file")
      .filter({ hasText: "recovered.txt" })
      .click();
    const route = await requested;
    await page.locator("#execution-new").click();
    await expect(page.locator("#execution-status")).toHaveText("准备就绪");
    const responseArrived = page.waitForResponse(
      (response) => response.url() === route.request().url(),
    );
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "late_fixture_error", message: "旧工作区预览失败" },
      }),
    });
    await (await responseArrived).finished();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(page.locator("#execution-preview-title")).toHaveText(
      "文件预览",
    );
    await expect(page.locator("#execution-preview")).toHaveText(
      "选择一个文本或代码文件查看内容。",
    );
    expect(calls).toBe(3);
    expect(errors).toEqual([]);
  } finally {
    await app?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
