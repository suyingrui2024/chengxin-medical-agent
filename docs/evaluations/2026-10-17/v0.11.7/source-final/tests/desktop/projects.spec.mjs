import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  readdir,
  rm,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

function respond(response, serial, tool) {
  const events = [
    {
      type: "message_start",
      message: {
        id: `project-${serial}`,
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
        ? { type: "tool_use", id: `project-${serial}`, ...tool }
        : { type: "text", text: "已实际修改并核对项目。" },
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

test("native project picker edits the chosen directory, delegates review and restores offline", async () => {
  test.setTimeout(60000);
  const temporary = await mkdtemp(
    path.join(os.tmpdir(), "chengxin-native-project-"),
  );
  const project = path.join(temporary, "sample-project");
  await mkdir(project);
  await writeFile(
    path.join(project, "calculator.py"),
    "def add(a, b):\n    return a - b\n",
  );
  await writeFile(path.join(project, "keep.txt"), "original");
  await writeFile(path.join(temporary, "outside.txt"), "outside-never-visible");
  let serial = 0,
    parent = 0,
    child = 0;
  const issues = [];
  const server = createServer(async (request, response) => {
    let text = "";
    for await (const chunk of request) text += chunk;
    const body = JSON.parse(text);
    const isRoot = JSON.stringify(body.messages).includes(
      "ROOT_PROJECT_FIXTURE",
    );
    let tool = null;
    if (isRoot) {
      const step = parent++;
      if (step === 0)
        tool = {
          name: "bash",
          input: {
            command: `python3 - <<'PY'\nfrom pathlib import Path\nassert not Path(${JSON.stringify(path.join(temporary, "outside.txt"))}).exists()\nassert not Path(${JSON.stringify(path.join(root, ".env"))}).exists()\np=Path('calculator.py')\np.write_text(p.read_text().replace('return a - b','return a + b'))\nfrom calculator import add\nassert add(2,3)==5\nassert Path('keep.txt').read_text()=='original'\nprint('project-edited-in-place')\nPY`,
            description:
              "Edit the explicitly selected project and execute its code",
          },
        };
      else if (step === 1)
        tool = {
          name: "subagent",
          input: {
            description: "Independent project review",
            prompt:
              "Use workspace_changes to inspect calculator.py; then independently execute its addition and save review.txt containing reviewed. Do not modify other files.",
            run_in_background: false,
          },
        };
      else if (step === 2)
        tool = { name: "workspace_changes", input: { path: "calculator.py" } };
    } else {
      const step = child++;
      if (step === 0)
        tool = { name: "workspace_changes", input: { path: "calculator.py" } };
      else if (step === 1) {
        if (!JSON.stringify(body.messages).includes("-    return a - b"))
          issues.push("child did not receive the original project diff");
        tool = {
          name: "bash",
          input: {
            command:
              "python3 -c \"from calculator import add;from pathlib import Path;assert add(-5,2)==-3;Path('review.txt').write_text('reviewed')\"",
            description:
              "Independently validate selected project and save a child artifact",
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
    MEDAGENT_LLM_API_KEY: "native-project-fixture-only",
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
  const picker = async (app, directory) =>
    app.evaluate(({ dialog }, target) => {
      dialog.showOpenDialog = async (_window, options) => {
        if (!options.properties.includes("openDirectory"))
          throw new Error("directory picker missing");
        return {
          canceled: target === null,
          filePaths: target === null ? [] : [target],
        };
      };
    }, directory);
  const enter = async (page) => {
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  };
  let app;
  try {
    app = await launch(environment);
    let page = await app.firstWindow();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await enter(page);
    await picker(app, null);
    await page.getByRole("button", { name: /打开本地项目/ }).click();
    await expect(page.locator("#execution-open-project")).toBeEnabled();
    expect(await page.locator("#execution-workspace option").count()).toBe(1);
    expect(serial).toBe(0);
    await picker(app, root);
    await page.getByRole("button", { name: /打开本地项目/ }).click();
    await expect(page.locator("#execution-feedback")).toContainText(
      "项目无法打开",
    );
    expect(serial).toBe(0);
    await picker(app, project);
    await page.getByRole("button", { name: /打开本地项目/ }).click();
    await expect(page.locator("#execution-directory")).toContainText(
      "文件直接保存到原目录",
    );
    await expect(page.locator("#execution-directory")).toContainText(project);
    expect(serial).toBe(0);
    expect((await readdir(project)).sort()).toEqual([
      "calculator.py",
      "keep.txt",
    ]);
    await expect(page.locator("#execution-workspace")).toHaveValue(
      /^[0-9a-f]{8}-[0-9a-f-]{27}$/,
    );
    const identity = await page.locator("#execution-workspace").inputValue();
    const rejected = await page.evaluate(
      async (directory) =>
        (
          await fetch("/api/execution", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ directory }),
          })
        ).status,
      project,
    );
    expect(rejected).toBe(400);
    await page
      .getByLabel("描述执行任务")
      .fill("ROOT_PROJECT_FIXTURE 修复加法并独立复核。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 20000,
    });
    expect(
      await readFile(path.join(project, "calculator.py"), "utf8"),
    ).toContain("return a + b");
    expect(await readFile(path.join(project, "review.txt"), "utf8")).toBe(
      "reviewed",
    );
    expect(await readFile(path.join(project, "keep.txt"), "utf8")).toBe(
      "original",
    );
    expect(serial).toBe(7);
    expect(child).toBe(3);
    expect(issues).toEqual([]);
    expect(await readdir(project)).not.toContain(".state");
    expect(await readdir(project)).not.toContain(".index");
    await page.getByRole("button", { name: /打开本地项目/ }).click();
    await expect(page.locator("#execution-workspace")).toHaveValue(identity);
    expect(serial).toBe(7);
    await page.getByRole("button", { name: /查看本轮变更/ }).click();
    await expect(page.locator("#execution-change-diff")).toContainText(
      "-    return a - b",
    );
    await expect(page.locator("#execution-change-diff")).toContainText(
      "+    return a + b",
    );
    await page.locator("#execution-change-close").click();
    await app.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0].setSize(900, 800);
    });
    await expect
      .poll(async () =>
        page.evaluate(
          () =>
            document.documentElement.scrollWidth <=
            document.documentElement.clientWidth,
        ),
      )
      .toBe(true);
    await expect(page.locator("#execution-open-project")).toBeVisible();
    await app.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0].setSize(1440, 980);
    });
    await page.getByRole("button", { name: /查看本轮变更/ }).click();
    await mkdir(path.join(root, "docs/assets/ui"), { recursive: true });
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/36-local-project-review.png"),
    });
    expect(errors).toEqual([]);
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
    await enter(page);
    await picker(app, project);
    await page.getByRole("button", { name: /打开本地项目/ }).click();
    await expect(page.locator("#execution-workspace")).toHaveValue(identity);
    await expect(page.locator("#execution-run")).toBeDisabled();
    await expect(page.locator("#execution-directory")).toContainText(project);
    await page.getByRole("button", { name: /查看本轮变更/ }).click();
    await expect(page.locator("#execution-change-diff")).toContainText(
      "-    return a - b",
    );
    expect(serial).toBe(7);
  } finally {
    await app?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
