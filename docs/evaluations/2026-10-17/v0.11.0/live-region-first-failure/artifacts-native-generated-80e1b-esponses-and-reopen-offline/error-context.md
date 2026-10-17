# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: artifacts.spec.mjs >> native generated plots and tables preview inertly, detect changed bytes, ignore late responses and reopen offline
- Location: tests/desktop/artifacts.spec.mjs:13:1

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: 0
Received: 1
```

# Test source

```ts
  53  |             input: {
  54  |               command: `python3 - <<'PY'\n${code}PY`,
  55  |               description: "Create actual synthetic plot and data files",
  56  |             },
  57  |           }
  58  |         : null;
  59  |     const block = tool
  60  |       ? { type: "tool_use", id: `artifact-${step}`, ...tool }
  61  |       : { type: "text", text: "已实际生成图表与数据表，均为研究产物。" };
  62  |     const events = [
  63  |       {
  64  |         type: "message_start",
  65  |         message: {
  66  |           id: `artifact-${step}`,
  67  |           model: "fixture",
  68  |           usage: { input_tokens: 10, output_tokens: 1 },
  69  |         },
  70  |       },
  71  |       { type: "content_block_start", index: 0, content_block: block },
  72  |       { type: "content_block_stop", index: 0 },
  73  |       {
  74  |         type: "message_delta",
  75  |         delta: { stop_reason: tool ? "tool_use" : "end_turn" },
  76  |         usage: { output_tokens: 10 },
  77  |       },
  78  |       { type: "message_stop" },
  79  |     ];
  80  |     response.writeHead(200, { "Content-Type": "text/event-stream" });
  81  |     response.end(
  82  |       events
  83  |         .map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`)
  84  |         .join(""),
  85  |     );
  86  |   });
  87  |   server.listen(0, "127.0.0.1");
  88  |   await once(server, "listening");
  89  |   const env = {
  90  |     ...process.env,
  91  |     MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
  92  |     MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
  93  |     MEDAGENT_DESKTOP_LIVE: "1",
  94  |     MEDAGENT_BACKEND: "dsh",
  95  |     MEDAGENT_LLM_BASE_URL: `http://127.0.0.1:${server.address().port}/anthropic`,
  96  |     MEDAGENT_LLM_API_KEY: "local-artifacts-fixture",
  97  |     MEDAGENT_LLM_MODEL: "fixture",
  98  |   };
  99  |   delete env.ELECTRON_RUN_AS_NODE;
  100 |   const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  101 |   let desktop;
  102 |   const launch = () =>
  103 |     electron.launch({
  104 |       executablePath,
  105 |       args: executablePath ? [] : [root],
  106 |       env,
  107 |     });
  108 |   const openWorkbench = async (page) => {
  109 |     await expect(page.getByText("本机服务已连接")).toBeVisible();
  110 |     await page.getByLabel("界面模式").selectOption("developer");
  111 |     await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  112 |   };
  113 |   const file = (page, name) =>
  114 |     page.locator(".execution-file").filter({ hasText: name });
  115 |   const loaded = async (page, width) => {
  116 |     await expect
  117 |       .poll(() =>
  118 |         page
  119 |           .locator("#execution-preview img")
  120 |           .evaluate((img) => img.complete && img.naturalWidth)
  121 |           .catch(() => 0),
  122 |       )
  123 |       .toBe(width);
  124 |   };
  125 |   try {
  126 |     desktop = await launch();
  127 |     let page = await desktop.firstWindow();
  128 |     const errors = [];
  129 |     page.on("pageerror", (error) => errors.push(error.message));
  130 |     await openWorkbench(page);
  131 |     await page
  132 |       .getByLabel("描述执行任务")
  133 |       .fill("生成可视化合成产物，实际执行后查看。");
  134 |     await page.getByRole("button", { name: "执行任务", exact: true }).click();
  135 |     await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
  136 |       timeout: 20000,
  137 |     });
  138 |     const directory = await page.locator("#execution-directory").textContent();
  139 |     const originalPng = await readFile(path.join(directory, "产物/趋势.png"));
  140 |     await file(page, "趋势.png").click();
  141 |     await loaded(page, 960);
  142 |     await expect(page.locator("#execution-preview-meta")).toContainText(
  143 |       "960 × 576",
  144 |     );
  145 |     const checked = page.waitForResponse((response) => response.url().includes("/artifact-status?"));
  146 |     await page.evaluate(() => {
  147 |       window.artifactMetaChanges = 0;
  148 |       window.artifactMetaObserver = new MutationObserver(() => window.artifactMetaChanges++);
  149 |       window.artifactMetaObserver.observe(document.getElementById("execution-preview-meta"), { childList:true, characterData:true, subtree:true });
  150 |     });
  151 |     await checked;
  152 |     await page.evaluate(() => new Promise(requestAnimationFrame));
> 153 |     expect(await page.evaluate(() => window.artifactMetaChanges)).toBe(0);
      |                                                                   ^ Error: expect(received).toBe(expected) // Object.is equality
  154 |     await page.evaluate(() => window.artifactMetaObserver.disconnect());
  155 |     const originalSrc = await page
  156 |       .locator("#execution-preview img")
  157 |       .getAttribute("src");
  158 |     await page.getByRole("button", { name: "放大查看" }).click();
  159 |     await expect(page.locator("#execution-artifact-dialog")).toBeVisible();
  160 |     await expect
  161 |       .poll(() =>
  162 |         page
  163 |           .locator("#execution-artifact-large img")
  164 |           .evaluate((img) => img.complete && img.naturalWidth),
  165 |       )
  166 |       .toBe(960);
  167 |     await page.screenshot({
  168 |       path: path.join(root, "docs/assets/ui/48-artifact-plot.png"),
  169 |       animations: "disabled",
  170 |     });
  171 |     await page.keyboard.press("Escape");
  172 |     await file(page, "换算.csv").click();
  173 |     await expect(page.locator("#execution-preview table")).toBeVisible();
  174 |     await expect(
  175 |       page.locator("#execution-preview td").filter({ hasText: "1250" }),
  176 |     ).toHaveCount(1);
  177 |     await page.getByRole("button", { name: "查看原文", exact: true }).click();
  178 |     await expect(page.locator("#execution-preview pre")).toContainText(
  179 |       "A,1.25,1250",
  180 |     );
  181 |     await page.getByRole("button", { name: "查看表格", exact: true }).click();
  182 |     await page.getByRole("button", { name: "放大查看" }).click();
  183 |     await page.screenshot({
  184 |       path: path.join(root, "docs/assets/ui/49-artifact-table.png"),
  185 |       animations: "disabled",
  186 |     });
  187 |     await page.keyboard.press("Escape");
  188 |     await file(page, "趋势.png").click();
  189 |     await loaded(page, 960);
  190 |     await desktop.evaluate(({ BrowserWindow }) =>
  191 |       BrowserWindow.getAllWindows()[0].setSize(900, 960),
  192 |     );
  193 |     await page.getByRole("button", { name: "放大查看" }).click();
  194 |     const dialog = page.locator("#execution-artifact-dialog");
  195 |     await expect(dialog).toBeVisible();
  196 |     expect(
  197 |       await dialog.evaluate((node) => node.getBoundingClientRect().width),
  198 |     ).toBeLessThan(900);
  199 |     await page.screenshot({
  200 |       path: path.join(root, "docs/assets/ui/50-artifact-narrow.png"),
  201 |       animations: "disabled",
  202 |     });
  203 |     await page.keyboard.press("Escape");
  204 |     await desktop.evaluate(({ BrowserWindow }) =>
  205 |       BrowserWindow.getAllWindows()[0].setSize(1440, 960),
  206 |     );
  207 |     await file(page, "photo.jpg").click();
  208 |     await loaded(page, 3);
  209 |     await file(page, "untrusted.csv").click();
  210 |     await expect(page.locator("#execution-preview")).toContainText(
  211 |       "<img src=x onerror=window.ARTIFACT_INJECTED=1>",
  212 |     );
  213 |     await expect(page.locator("#execution-preview img")).toHaveCount(0);
  214 |     await file(page, "unsafe.svg").click();
  215 |     await expect(page.locator("#execution-preview")).toContainText(
  216 |       '<svg onload="window.ARTIFACT_INJECTED=1">',
  217 |     );
  218 |     expect(await page.evaluate(() => window.ARTIFACT_INJECTED)).toBeUndefined();
  219 |     await file(page, "趋势.png").click();
  220 |     await loaded(page, 960);
  221 |     const small = await readFile(
  222 |       path.join(root, "tests/fixtures/artifact-preview.jpg"),
  223 |     );
  224 |     await writeFile(path.join(directory, "产物/趋势.png"), small);
  225 |     await expect(page.locator("#execution-preview-meta")).toContainText(
  226 |       "文件已变化",
  227 |       { timeout: 6000 },
  228 |     );
  229 |     await expect(file(page, "趋势.png")).toBeFocused();
  230 |     const stale = await page.evaluate(async (src) => {
  231 |       const r = await fetch(src);
  232 |       return r.status;
  233 |     }, originalSrc);
  234 |     expect(stale).toBe(400);
  235 |     await page.getByRole("button", { name: "重新读取", exact: true }).click();
  236 |     await expect(page.locator("#execution-preview")).toContainText(
  237 |       "PNG签名无效",
  238 |     );
  239 |     await writeFile(path.join(directory, "产物/趋势.png"), originalPng);
  240 |     await page.getByRole("button", { name: "重新读取", exact: true }).click();
  241 |     await loaded(page, 960);
  242 |     let pending;
  243 |     const requested = new Promise((resolve) => {
  244 |       pending = resolve;
  245 |     });
  246 |     await page.route("**/api/execution/*/preview?path=*", (route) => {
  247 |       if (decodeURIComponent(route.request().url()).includes("产物/趋势.png"))
  248 |         pending(route);
  249 |       else route.continue();
  250 |     });
  251 |     await file(page, "趋势.png").click();
  252 |     const route = await requested;
  253 |     await file(page, "换算.csv").click();
```