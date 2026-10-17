# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: directory.spec.mjs >> native directory pages reach deep files, expose incomplete coverage, reject stale pages and ignore old workspace responses
- Location: tests/desktop/directory.spec.mjs:12:1

# Error details

```
Error: expect(locator).toHaveText(expected) failed

Locator:  locator('#execution-file-count')
Expected: "—"
Received: "1"
Timeout:  5000ms

Call log:
  - Expect "toHaveText" locator('#execution-file-count') with timeout 5000ms
  - waiting for locator('#execution-file-count')
    14 × locator resolved to <span class="count" id="execution-file-count">1</span>
       - unexpected value "1"

```

```yaml
- text: "1"
```

# Test source

```ts
  51  |           id: `directory-${serial}`,
  52  |           model: "fixture",
  53  |           usage: { input_tokens: 10, output_tokens: 1 },
  54  |         },
  55  |       },
  56  |       { type: "content_block_start", index: 0, content_block: block },
  57  |       { type: "content_block_stop", index: 0 },
  58  |       {
  59  |         type: "message_delta",
  60  |         delta: { stop_reason: serial === 1 ? "tool_use" : "end_turn" },
  61  |         usage: { output_tokens: 10 },
  62  |       },
  63  |       { type: "message_stop" },
  64  |     ];
  65  |     response.writeHead(200, { "Content-Type": "text/event-stream" });
  66  |     response.end(
  67  |       events
  68  |         .map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`)
  69  |         .join(""),
  70  |     );
  71  |   });
  72  |   server.listen(0, "127.0.0.1");
  73  |   await once(server, "listening");
  74  |   const env = {
  75  |     ...process.env,
  76  |     MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
  77  |     MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
  78  |     MEDAGENT_DESKTOP_LIVE: "1",
  79  |     MEDAGENT_BACKEND: "dsh",
  80  |     MEDAGENT_LLM_BASE_URL: `http://127.0.0.1:${server.address().port}/anthropic`,
  81  |     MEDAGENT_LLM_API_KEY: "local-directory-fixture",
  82  |     MEDAGENT_LLM_MODEL: "fixture",
  83  |   };
  84  |   delete env.ELECTRON_RUN_AS_NODE;
  85  |   const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  86  |   const launch = () =>
  87  |     electron.launch({
  88  |       executablePath,
  89  |       args: executablePath ? [] : [root],
  90  |       env,
  91  |     });
  92  |   const workbench = async (page) => {
  93  |     await expect(page.getByText("本机服务已连接")).toBeVisible();
  94  |     await page.getByLabel("界面模式").selectOption("developer");
  95  |     await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  96  |   };
  97  |   const file = (page, name) =>
  98  |     page.locator(".execution-file").filter({ hasText: name });
  99  |   try {
  100 |     desktop = await launch();
  101 |     let page = await desktop.firstWindow();
  102 |     const errors = [];
  103 |     page.on("pageerror", (e) => errors.push(e.message));
  104 |     await workbench(page);
  105 |     await page
  106 |       .getByLabel("描述执行任务")
  107 |       .fill("创建目录浏览的合成夹具，不联网。");
  108 |     await page.getByRole("button", { name: "执行任务", exact: true }).click();
  109 |     await expect(page.locator("#execution-status")).toHaveText("本轮已完成");
  110 |     const directory = await page.locator("#execution-directory").textContent(),
  111 |       identity = path.basename(directory);
  112 |     await expect(page.locator("#execution-file-count")).toHaveText("262");
  113 |     await expect(page.locator("#execution-files button")).toHaveCount(100);
  114 |     await page.getByRole("button", { name: "下一页", exact: true }).click();
  115 |     await expect(page.locator("#execution-directory-page")).toHaveText(
  116 |       "第 2 页",
  117 |     );
  118 |     await page.getByRole("button", { name: "下一页", exact: true }).click();
  119 |     await expect(page.locator("#execution-directory-page")).toHaveText(
  120 |       "第 3 页",
  121 |     );
  122 |     await file(page, "item-259.txt").click();
  123 |     await expect(page.locator("#execution-preview")).toHaveText("259");
  124 |     await expect(file(page, "outside-link")).toBeDisabled();
  125 |     await page.screenshot({
  126 |       path: path.join(root, "docs/assets/ui/51-directory-pagination.png"),
  127 |       animations: "disabled",
  128 |     });
  129 |     await writeFile(path.join(directory, "aaa-added.txt"), "new entry");
  130 |     await expect(page.locator("#execution-directory-page")).toHaveText(
  131 |       "第 1 页",
  132 |       { timeout: 7000 },
  133 |     );
  134 |     await expect(page.locator("#execution-directory-status")).toContainText(
  135 |       "目录条目变化",
  136 |     );
  137 |     await page.getByLabel("显示隐藏与缓存").check();
  138 |     await expect(file(page, ".hidden.txt")).toBeVisible();
  139 |     await page.locator(".directory-direct summary").click();
  140 |     await page.getByLabel("文件相对路径").fill(`${deep}/result.csv`);
  141 |     await page.getByRole("button", { name: "打开文件", exact: true }).click();
  142 |     await expect(page.locator("#execution-preview table")).toContainText(
  143 |       "1250",
  144 |     );
  145 |     await expect(page.locator("#execution-directory-breadcrumb")).toContainText(
  146 |       "level12",
  147 |     );
  148 |     const nested = path.join(directory, deep);
  149 |     await rename(nested, `${nested}-moved`);
  150 |     await expect(page.locator("#execution-directory-status")).toContainText("目录未能读取", { timeout: 6000 });
> 151 |     await expect(page.locator("#execution-file-count")).toHaveText("—");
      |                                                         ^ Error: expect(locator).toHaveText(expected) failed
  152 |     await expect(page.locator("#execution-files button")).toHaveCount(0);
  153 |     await rename(`${nested}-moved`, nested);
  154 |     await expect(file(page, "result.csv")).toBeVisible({ timeout: 6000 });
  155 |     await expect(page.locator("#execution-file-count")).toHaveText("1");
  156 |     await file(page, "result.csv").click();
  157 |     await expect(page.locator("#execution-preview table")).toContainText("1250");
  158 |     await page
  159 |       .getByRole("button", { name: "浏览目录 工作区根目录", exact: true })
  160 |       .click();
  161 |     const checked = page.waitForResponse((r) =>
  162 |       r.url().includes("/artifact-status?"),
  163 |     );
  164 |     await checked;
  165 |     await expect(page.locator("#execution-preview-meta")).not.toContainText(
  166 |       "已移除",
  167 |     );
  168 |     await expect(page.locator("#execution-preview table")).toContainText(
  169 |       "1250",
  170 |     );
  171 |     invalidName = Buffer.concat([
  172 |       Buffer.from(directory + "/invalid-"),
  173 |       Buffer.from([0xff]),
  174 |     ]);
  175 |     await writeFile(invalidName, "synthetic");
  176 |     await expect(page.locator("#execution-directory-status")).toContainText(
  177 |       "目录扫描不完整",
  178 |       { timeout: 6000 },
  179 |     );
  180 |     await expect(page.locator("#execution-file-count")).toHaveText("—");
  181 |     await page.screenshot({
  182 |       path: path.join(root, "docs/assets/ui/52-directory-coverage.png"),
  183 |       animations: "disabled",
  184 |     });
  185 |     await rm(invalidName);
  186 |     invalidName = null;
  187 |     await expect(page.locator("#execution-directory-status")).not.toContainText(
  188 |       "扫描不完整",
  189 |       { timeout: 6000 },
  190 |     );
  191 |     let release;
  192 |     let held = false;
  193 |     const requested = new Promise((resolve) => {
  194 |       release = resolve;
  195 |     });
  196 |     await page.route("**/api/execution/*/directory?*", (route) => {
  197 |       if (route.request().url().includes(identity) && !held) {
  198 |         held = true;
  199 |         release(route);
  200 |       } else route.continue();
  201 |     });
  202 |     await page.getByLabel("文件相对路径").focus();
  203 |     const old = await requested;
  204 |     await expect(page.getByLabel("文件相对路径")).toBeEnabled();
  205 |     await expect(page.getByLabel("文件相对路径")).toBeFocused();
  206 |     await page.keyboard.press("End");
  207 |     await page.keyboard.type("x");
  208 |     await expect(page.getByLabel("文件相对路径")).toHaveValue(
  209 |       `${deep}/result.csvx`,
  210 |     );
  211 |     await page.getByRole("button", { name: "下一页", exact: true }).click();
  212 |     await expect(page.locator("#execution-directory-page")).toHaveText(
  213 |       "第 2 页",
  214 |     );
  215 |     await page.locator("#execution-new").click();
  216 |     await expect(page.locator("#execution-status")).toHaveText("准备就绪");
  217 |     await expect(page.locator("#execution-file-count")).toHaveText("0");
  218 |     await old.fulfill({
  219 |       status: 200,
  220 |       contentType: "application/json",
  221 |       body: JSON.stringify({
  222 |         schema_version: "execution-directory/v1",
  223 |         path: "",
  224 |         entries: [
  225 |           {
  226 |             name: "phantom.txt",
  227 |             path: "phantom.txt",
  228 |             kind: "file",
  229 |             size: 1,
  230 |             revision: "old",
  231 |           },
  232 |         ],
  233 |         scan_complete: true,
  234 |         total_entries: 1,
  235 |         reasons: [],
  236 |         next_cursor: null,
  237 |       }),
  238 |     });
  239 |     await page.evaluate(() => new Promise(requestAnimationFrame));
  240 |     await expect(file(page, "phantom.txt")).toHaveCount(0);
  241 |     await expect(page.locator("#execution-file-count")).toHaveText("0");
  242 |     expect(calls).toBe(2);
  243 |     expect(errors).toEqual([]);
  244 |     await desktop.close();
  245 |     desktop = null;
  246 |     delete env.MEDAGENT_LLM_API_KEY;
  247 |     delete env.MEDAGENT_DESKTOP_LIVE;
  248 |     env.MEDAGENT_BACKEND = "demo";
  249 |     desktop = await launch();
  250 |     page = await desktop.firstWindow();
  251 |     await workbench(page);
```