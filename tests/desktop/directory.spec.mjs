import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import { mkdtemp, writeFile, rm, rename } from "node:fs/promises";
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
test("native directory pages reach deep files, expose incomplete coverage, reject stale pages and ignore old workspace responses", async () => {
  test.setTimeout(60000);
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-directory-"),
  );
  let calls = 0,
    desktop,
    invalidName;
  const deep =
    "results/" + Array.from({ length: 13 }, (_, i) => `level${i}`).join("/");
  const code = `from pathlib import Path
for i in range(260):Path(f'item-{i:03}.txt').write_text(str(i))
p=Path('${deep}');p.mkdir(parents=True)
(p/'result.csv').write_text('sample,value'+chr(10)+'A,1250'+chr(10),encoding='utf-8')
Path('.hidden.txt').write_text('synthetic hidden file')
Path('outside-link').symlink_to('/etc',target_is_directory=True)
print('large and deep fixtures created')
`;
  const server = createServer(async (request, response) => {
    for await (const _part of request) {
      /* Drain synthetic fixture request. */
    }
    const serial = ++calls;
    const block =
      serial === 1
        ? {
            type: "tool_use",
            id: "directory-create",
            name: "bash",
            input: {
              command: `python3 - <<'PY'\n${code}PY`,
              description: "Create actual directory fixtures",
            },
          }
        : { type: "text", text: "目录夹具已创建。" };
    const events = [
      {
        type: "message_start",
        message: {
          id: `directory-${serial}`,
          model: "fixture",
          usage: { input_tokens: 10, output_tokens: 1 },
        },
      },
      { type: "content_block_start", index: 0, content_block: block },
      { type: "content_block_stop", index: 0 },
      {
        type: "message_delta",
        delta: { stop_reason: serial === 1 ? "tool_use" : "end_turn" },
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
    MEDAGENT_LLM_API_KEY: "local-directory-fixture",
    MEDAGENT_LLM_MODEL: "fixture",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  const launch = () =>
    electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env,
    });
  const workbench = async (page) => {
    await expect(page.getByText("本机服务已连接")).toBeVisible();
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  };
  const file = (page, name) =>
    page.locator(".execution-file").filter({ hasText: name });
  try {
    desktop = await launch();
    let page = await desktop.firstWindow();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await workbench(page);
    await page
      .getByLabel("描述执行任务")
      .fill("创建目录浏览的合成夹具，不联网。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成");
    const directory = await page.locator("#execution-directory").textContent(),
      identity = path.basename(directory);
    await expect(page.locator("#execution-file-count")).toHaveText("262");
    await expect(page.locator("#execution-files button")).toHaveCount(100);
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(page.locator("#execution-directory-page")).toHaveText(
      "第 2 页",
    );
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(page.locator("#execution-directory-page")).toHaveText(
      "第 3 页",
    );
    await file(page, "item-259.txt").click();
    await expect(page.locator("#execution-preview")).toHaveText("259");
    await expect(file(page, "outside-link")).toBeDisabled();
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/51-directory-pagination.png"),
      animations: "disabled",
    });
    await writeFile(path.join(directory, "aaa-added.txt"), "new entry");
    await expect(page.locator("#execution-directory-page")).toHaveText(
      "第 1 页",
      { timeout: 7000 },
    );
    await expect(page.locator("#execution-directory-status")).toContainText(
      "目录条目变化",
    );
    await page.getByLabel("显示隐藏与缓存").check();
    await expect(file(page, ".hidden.txt")).toBeVisible();
    await page.locator(".directory-direct summary").click();
    await page.getByLabel("文件相对路径").fill(`${deep}/result.csv`);
    await page.getByRole("button", { name: "打开文件", exact: true }).click();
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    await expect(page.locator("#execution-directory-breadcrumb")).toContainText(
      "level12",
    );
    const nested = path.join(directory, deep);
    await rename(nested, `${nested}-moved`);
    await expect(page.locator("#execution-directory-status")).toContainText(
      "目录未能读取",
      { timeout: 6000 },
    );
    await expect(page.locator("#execution-file-count")).toHaveText("—");
    await expect(page.locator("#execution-files button")).toHaveCount(0);
    await rename(`${nested}-moved`, nested);
    await expect(file(page, "result.csv")).toBeVisible({ timeout: 6000 });
    await expect(page.locator("#execution-file-count")).toHaveText("1");
    await file(page, "result.csv").click();
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    await page
      .getByRole("button", { name: "浏览目录 工作区根目录", exact: true })
      .click();
    const checked = page.waitForResponse((r) =>
      r.url().includes("/artifact-status?"),
    );
    await checked;
    await expect(page.locator("#execution-preview-meta")).not.toContainText(
      "已移除",
    );
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    invalidName = Buffer.concat([
      Buffer.from(directory + "/invalid-"),
      Buffer.from([0xff]),
    ]);
    await writeFile(invalidName, "synthetic");
    await expect(page.locator("#execution-directory-status")).toContainText(
      "目录扫描不完整",
      { timeout: 6000 },
    );
    await expect(page.locator("#execution-file-count")).toHaveText("—");
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/52-directory-coverage.png"),
      animations: "disabled",
    });
    await rm(invalidName);
    invalidName = null;
    await expect(page.locator("#execution-directory-status")).not.toContainText(
      "扫描不完整",
      { timeout: 6000 },
    );
    let release;
    let held = false;
    const requested = new Promise((resolve) => {
      release = resolve;
    });
    await page.route("**/api/execution/*/directory?*", (route) => {
      if (route.request().url().includes(identity) && !held) {
        held = true;
        release(route);
      } else route.continue();
    });
    await page.getByLabel("文件相对路径").focus();
    const old = await requested;
    await expect(page.getByLabel("文件相对路径")).toBeEnabled();
    await expect(page.getByLabel("文件相对路径")).toBeFocused();
    await page.keyboard.press("End");
    await page.keyboard.type("x");
    await expect(page.getByLabel("文件相对路径")).toHaveValue(
      `${deep}/result.csvx`,
    );
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(page.locator("#execution-directory-page")).toHaveText(
      "第 2 页",
    );
    await page.locator("#execution-new").click();
    await expect(page.locator("#execution-status")).toHaveText("准备就绪");
    await expect(page.locator("#execution-file-count")).toHaveText("0");
    await old.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "execution-directory/v1",
        path: "",
        entries: [
          {
            name: "phantom.txt",
            path: "phantom.txt",
            kind: "file",
            size: 1,
            revision: "old",
          },
        ],
        scan_complete: true,
        total_entries: 1,
        reasons: [],
        next_cursor: null,
      }),
    });
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(file(page, "phantom.txt")).toHaveCount(0);
    await expect(page.locator("#execution-file-count")).toHaveText("0");
    expect(calls).toBe(2);
    expect(errors).toEqual([]);
    await desktop.close();
    desktop = null;
    delete env.MEDAGENT_LLM_API_KEY;
    delete env.MEDAGENT_DESKTOP_LIVE;
    env.MEDAGENT_BACKEND = "demo";
    desktop = await launch();
    page = await desktop.firstWindow();
    await workbench(page);
    await page.getByLabel("选择执行工作区").selectOption(identity);
    await page.locator(".directory-direct summary").click();
    await page.getByLabel("文件相对路径").fill(`${deep}/result.csv`);
    await page.getByRole("button", { name: "打开文件", exact: true }).click();
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    expect(calls).toBe(2);
  } finally {
    await desktop?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    if (invalidName) await rm(invalidName, { force: true });
    await rm(temporary, { recursive: true, force: true });
  }
});
