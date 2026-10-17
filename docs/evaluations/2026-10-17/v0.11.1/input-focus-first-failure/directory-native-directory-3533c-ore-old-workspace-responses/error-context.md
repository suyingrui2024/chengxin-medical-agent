# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: directory.spec.mjs >> native directory pages reach deep files, expose incomplete coverage, reject stale pages and ignore old workspace responses
- Location: tests/desktop/directory.spec.mjs:12:1

# Error details

```
Error: expect(locator).toBeEnabled() failed

Locator:  getByLabel('文件相对路径')
Expected: enabled
Received: disabled
Timeout:  5000ms

Call log:
  - Expect "toBeEnabled" getByLabel('文件相对路径') with timeout 5000ms
  - waiting for getByLabel('文件相对路径')
    14 × locator resolved to <input disabled maxlength="4096" spellcheck="false" autocomplete="off" aria-label="文件相对路径" id="execution-file-path" placeholder="例如 outputs/chart.png"/>
       - unexpected value "disabled"

```

```yaml
- textbox "文件相对路径" [disabled]:
  - /placeholder: 例如 outputs/chart.png
  - text: results/level0/level1/level2/level3/level4/level5/level6/level7/level8/level9/level10/level11/level12/result.csv
```

# Test source

```ts
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
  148 |     await page
  149 |       .getByRole("button", { name: "浏览目录 工作区根目录", exact: true })
  150 |       .click();
  151 |     const checked = page.waitForResponse((r) =>
  152 |       r.url().includes("/artifact-status?"),
  153 |     );
  154 |     await checked;
  155 |     await expect(page.locator("#execution-preview-meta")).not.toContainText(
  156 |       "已移除",
  157 |     );
  158 |     await expect(page.locator("#execution-preview table")).toContainText(
  159 |       "1250",
  160 |     );
  161 |     invalidName = Buffer.concat([
  162 |       Buffer.from(directory + "/invalid-"),
  163 |       Buffer.from([0xff]),
  164 |     ]);
  165 |     await writeFile(invalidName, "synthetic");
  166 |     await expect(page.locator("#execution-directory-status")).toContainText(
  167 |       "目录扫描不完整",
  168 |       { timeout: 6000 },
  169 |     );
  170 |     await expect(page.locator("#execution-file-count")).toHaveText("—");
  171 |     await page.screenshot({
  172 |       path: path.join(root, "docs/assets/ui/52-directory-coverage.png"),
  173 |       animations: "disabled",
  174 |     });
  175 |     await rm(invalidName);
  176 |     invalidName = null;
  177 |     await expect(page.locator("#execution-directory-status")).not.toContainText("扫描不完整", {timeout:6000});
  178 |     let release;
  179 |     let held = false;
  180 |     const requested = new Promise((resolve) => {
  181 |       release = resolve;
  182 |     });
  183 |     await page.route("**/api/execution/*/directory?*", (route) => {
  184 |       if (route.request().url().includes(identity) && !held) { held = true; release(route); }
  185 |       else route.continue();
  186 |     });
  187 |     await page.getByLabel("文件相对路径").focus();
  188 |     const old = await requested;
> 189 |     await expect(page.getByLabel("文件相对路径")).toBeEnabled();
      |                                             ^ Error: expect(locator).toBeEnabled() failed
  190 |     await expect(page.getByLabel("文件相对路径")).toBeFocused();
  191 |     await page.keyboard.press("End");await page.keyboard.type("x");
  192 |     await expect(page.getByLabel("文件相对路径")).toHaveValue(`${deep}/result.csvx`);
  193 |     await page.getByRole("button", {name:"下一页",exact:true}).click();
  194 |     await expect(page.locator("#execution-directory-page")).toHaveText("第 2 页");
  195 |     await page.locator("#execution-new").click();
  196 |     await expect(page.locator("#execution-status")).toHaveText("准备就绪");
  197 |     await expect(page.locator("#execution-file-count")).toHaveText("0");
  198 |     await old.fulfill({
  199 |       status: 200,
  200 |       contentType: "application/json",
  201 |       body: JSON.stringify({
  202 |         schema_version: "execution-directory/v1",
  203 |         path: "",
  204 |         entries: [
  205 |           {
  206 |             name: "phantom.txt",
  207 |             path: "phantom.txt",
  208 |             kind: "file",
  209 |             size: 1,
  210 |             revision: "old",
  211 |           },
  212 |         ],
  213 |         scan_complete: true,
  214 |         total_entries: 1,
  215 |         reasons: [],
  216 |         next_cursor: null,
  217 |       }),
  218 |     });
  219 |     await page.evaluate(() => new Promise(requestAnimationFrame));
  220 |     await expect(file(page, "phantom.txt")).toHaveCount(0);
  221 |     await expect(page.locator("#execution-file-count")).toHaveText("0");
  222 |     expect(calls).toBe(2);
  223 |     expect(errors).toEqual([]);
  224 |     await desktop.close();
  225 |     desktop = null;
  226 |     delete env.MEDAGENT_LLM_API_KEY;
  227 |     delete env.MEDAGENT_DESKTOP_LIVE;
  228 |     env.MEDAGENT_BACKEND = "demo";
  229 |     desktop = await launch();
  230 |     page = await desktop.firstWindow();
  231 |     await workbench(page);
  232 |     await page.getByLabel("选择执行工作区").selectOption(identity);
  233 |     await page.locator(".directory-direct summary").click();
  234 |     await page.getByLabel("文件相对路径").fill(`${deep}/result.csv`);
  235 |     await page.getByRole("button", { name: "打开文件", exact: true }).click();
  236 |     await expect(page.locator("#execution-preview table")).toContainText(
  237 |       "1250",
  238 |     );
  239 |     expect(calls).toBe(2);
  240 |   } finally {
  241 |     await desktop?.close();
  242 |     server.closeAllConnections();
  243 |     await new Promise((resolve) => server.close(resolve));
  244 |     if (invalidName) await rm(invalidName, { force: true });
  245 |     await rm(temporary, { recursive: true, force: true });
  246 |   }
  247 | });
  248 | 
```