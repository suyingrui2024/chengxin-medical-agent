import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
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

test("native generated plots and tables preview inertly, detect changed bytes, ignore late responses and reopen offline", async () => {
  test.setTimeout(60000);
  const temporary = await mkdtemp(
    path.join(root, ".runtime/native-artifacts-"),
  );
  const jpeg = (
    await readFile(path.join(root, "tests/fixtures/artifact-preview.jpg"))
  ).toString("base64");
  const code = `from pathlib import Path
import base64,csv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
out=Path('产物');out.mkdir(exist_ok=True)
with (out/'换算.csv').open('w',encoding='utf-8',newline='') as f:
 w=csv.writer(f);w.writerow(['样本','原量（kg）','换算（g）']);w.writerows([['A','1.25','1250'],['B','2.00','2000'],['C','0.75','750']])
fig,ax=plt.subplots(figsize=(8,4.8),dpi=120)
fig.patch.set_facecolor('#fbfcf8');ax.set_facecolor('#fbfcf8')
bars=ax.bar(['A','B','C'],[1250,2000,750],color=['#397c67','#235a49','#8cae71'],width=.52)
ax.bar_label(bars,padding=5);ax.set_ylim(0,2400)
ax.set_ylabel('Mass (g)');ax.set_xlabel('Sample')
ax.set_title('Unit conversion / synthetic data',loc='left',pad=20)
ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
fig.tight_layout();fig.savefig(out/'趋势.png');plt.close(fig)
(out/'photo.jpg').write_bytes(base64.b64decode('${jpeg}'))
with (out/'untrusted.csv').open('w',encoding='utf-8',newline='') as f:
 csv.writer(f).writerows([['name','value'],['<img src=x onerror=window.ARTIFACT_INJECTED=1>','=1+1']])
(out/'unsafe.svg').write_text('<svg onload="window.ARTIFACT_INJECTED=1"></svg>')
print('actual plot and tables saved')
`;
  let calls = 0;
  const server = createServer(async (request, response) => {
    for await (const _part of request) {
      /* Drain local fixture request. */
    }
    const step = ++calls;
    const tool =
      step === 1
        ? {
            name: "bash",
            input: {
              command: `python3 - <<'PY'\n${code}PY`,
              description: "Create actual synthetic plot and data files",
            },
          }
        : null;
    const block = tool
      ? { type: "tool_use", id: `artifact-${step}`, ...tool }
      : { type: "text", text: "已实际生成图表与数据表，均为研究产物。" };
    const events = [
      {
        type: "message_start",
        message: {
          id: `artifact-${step}`,
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
    MEDAGENT_LLM_API_KEY: "local-artifacts-fixture",
    MEDAGENT_LLM_MODEL: "fixture",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  let desktop;
  const launch = () =>
    electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env,
    });
  const openWorkbench = async (page) => {
    await expect(page.getByText("本机服务已连接")).toBeVisible();
    await page.getByLabel("界面模式").selectOption("developer");
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  };
  const file = (page, name) =>
    page.locator(".execution-file").filter({ hasText: name });
  const loaded = async (page, width) => {
    await expect
      .poll(() =>
        page
          .locator("#execution-preview img")
          .evaluate((img) => img.complete && img.naturalWidth)
          .catch(() => 0),
      )
      .toBe(width);
  };
  try {
    desktop = await launch();
    let page = await desktop.firstWindow();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await openWorkbench(page);
    await page
      .getByLabel("描述执行任务")
      .fill("生成可视化合成产物，实际执行后查看。");
    await page.getByRole("button", { name: "执行任务", exact: true }).click();
    await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
      timeout: 20000,
    });
    const directory = await page.locator("#execution-directory").textContent();
    const originalPng = await readFile(path.join(directory, "产物/趋势.png"));
    await page
      .getByRole("button", { name: "进入目录 产物", exact: true })
      .click();
    await file(page, "趋势.png").click();
    await loaded(page, 960);
    await expect(page.locator("#execution-preview-meta")).toContainText(
      "960 × 576",
    );
    const checked = page.waitForResponse((response) =>
      response.url().includes("/artifact-status?"),
    );
    await page.evaluate(() => {
      window.artifactMetaChanges = 0;
      window.artifactMetaObserver = new MutationObserver(
        () => window.artifactMetaChanges++,
      );
      window.artifactMetaObserver.observe(
        document.getElementById("execution-preview-meta"),
        {
          childList: true,
          characterData: true,
          subtree: true,
          attributes: true,
        },
      );
    });
    await checked;
    await page.evaluate(() => new Promise(requestAnimationFrame));
    expect(await page.evaluate(() => window.artifactMetaChanges)).toBe(0);
    await page.evaluate(() => window.artifactMetaObserver.disconnect());
    const originalSrc = await page
      .locator("#execution-preview img")
      .getAttribute("src");
    await page.getByRole("button", { name: "放大查看" }).click();
    await expect(page.locator("#execution-artifact-dialog")).toBeVisible();
    await expect
      .poll(() =>
        page
          .locator("#execution-artifact-large img")
          .evaluate((img) => img.complete && img.naturalWidth),
      )
      .toBe(960);
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/48-artifact-plot.png"),
      animations: "disabled",
    });
    await page.keyboard.press("Escape");
    await file(page, "换算.csv").click();
    await expect(page.locator("#execution-preview table")).toBeVisible();
    await expect(
      page.locator("#execution-preview td").filter({ hasText: "1250" }),
    ).toHaveCount(1);
    await page.getByRole("button", { name: "查看原文", exact: true }).click();
    await expect(page.locator("#execution-preview pre")).toContainText(
      "A,1.25,1250",
    );
    await page.getByRole("button", { name: "查看表格", exact: true }).click();
    await page.getByRole("button", { name: "放大查看" }).click();
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/49-artifact-table.png"),
      animations: "disabled",
    });
    await page.keyboard.press("Escape");
    await file(page, "趋势.png").click();
    await loaded(page, 960);
    await desktop.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].setSize(900, 960),
    );
    await page.getByRole("button", { name: "放大查看" }).click();
    const dialog = page.locator("#execution-artifact-dialog");
    await expect(dialog).toBeVisible();
    expect(
      await dialog.evaluate((node) => node.getBoundingClientRect().width),
    ).toBeLessThan(900);
    await page.screenshot({
      path: path.join(root, "docs/assets/ui/50-artifact-narrow.png"),
      animations: "disabled",
    });
    await page.keyboard.press("Escape");
    await desktop.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].setSize(1440, 960),
    );
    await file(page, "photo.jpg").click();
    await loaded(page, 3);
    await file(page, "untrusted.csv").click();
    await expect(page.locator("#execution-preview")).toContainText(
      "<img src=x onerror=window.ARTIFACT_INJECTED=1>",
    );
    await expect(page.locator("#execution-preview img")).toHaveCount(0);
    await file(page, "unsafe.svg").click();
    await expect(page.locator("#execution-preview")).toContainText(
      '<svg onload="window.ARTIFACT_INJECTED=1">',
    );
    expect(await page.evaluate(() => window.ARTIFACT_INJECTED)).toBeUndefined();
    await file(page, "趋势.png").click();
    await loaded(page, 960);
    const small = await readFile(
      path.join(root, "tests/fixtures/artifact-preview.jpg"),
    );
    await writeFile(path.join(directory, "产物/趋势.png"), small);
    await expect(page.locator("#execution-preview-meta")).toContainText(
      "文件已变化",
      { timeout: 6000 },
    );
    await expect(file(page, "趋势.png")).toBeFocused();
    const stale = await page.evaluate(async (src) => {
      const r = await fetch(src);
      return r.status;
    }, originalSrc);
    expect(stale).toBe(400);
    await page.getByRole("button", { name: "重新读取", exact: true }).click();
    await expect(page.locator("#execution-preview")).toContainText(
      "PNG签名无效",
    );
    await writeFile(path.join(directory, "产物/趋势.png"), originalPng);
    await page.getByRole("button", { name: "重新读取", exact: true }).click();
    await loaded(page, 960);
    let pending;
    const requested = new Promise((resolve) => {
      pending = resolve;
    });
    await page.route("**/api/execution/*/preview?path=*", (route) => {
      if (decodeURIComponent(route.request().url()).includes("产物/趋势.png"))
        pending(route);
      else route.continue();
    });
    await file(page, "趋势.png").click();
    const route = await requested;
    await file(page, "换算.csv").click();
    await expect(page.locator("#execution-preview table")).toBeVisible();
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "late_image_error", message: "过期图片错误" },
      }),
    });
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(page.locator("#execution-preview-title")).toHaveText(
      "产物/换算.csv",
    );
    await expect(page.locator("#execution-preview table")).toBeVisible();
    expect(calls).toBe(2);
    expect(errors).toEqual([]);
    await desktop.close();
    desktop = null;
    delete env.MEDAGENT_LLM_API_KEY;
    delete env.MEDAGENT_DESKTOP_LIVE;
    env.MEDAGENT_BACKEND = "demo";
    desktop = await launch();
    page = await desktop.firstWindow();
    await openWorkbench(page);
    await page
      .getByLabel("选择执行工作区")
      .selectOption(path.basename(directory));
    await page
      .getByRole("button", { name: "进入目录 产物", exact: true })
      .click();
    await file(page, "换算.csv").click();
    await expect(page.locator("#execution-preview table")).toBeVisible();
    expect(calls).toBe(2);
  } finally {
    await desktop?.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(temporary, { recursive: true, force: true });
  }
});
