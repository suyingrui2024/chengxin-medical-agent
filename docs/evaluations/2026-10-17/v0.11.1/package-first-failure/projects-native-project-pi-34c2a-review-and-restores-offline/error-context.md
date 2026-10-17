# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: projects.spec.mjs >> native project picker edits the chosen directory, delegates review and restores offline
- Location: tests/desktop/projects.spec.mjs:57:1

# Error details

```
Error: expect(locator).toHaveValue(expected) failed

Locator:  locator('#execution-workspace')
Expected: ""
Received: "9cc9462d-cdb3-4f34-b822-ab93ea4b49c4"
Timeout:  5000ms

Call log:
  - Expect "toHaveValue" locator('#execution-workspace') with timeout 5000ms
  - waiting for locator('#execution-workspace')
    14 × locator resolved to <select aria-label="选择执行工作区" id="execution-workspace">…</select>
       - unexpected value "9cc9462d-cdb3-4f34-b822-ab93ea4b49c4"

```

```yaml
- combobox "选择执行工作区":
  - option "选择已保存的工作区"
  - option "ROOT_PROJECT_FIXTURE 修复加法并独立复核。 · 本轮已完成" [selected]
```

# Test source

```ts
  124 |   });
  125 |   server.listen(0, "127.0.0.1");
  126 |   await once(server, "listening");
  127 |   const environment = {
  128 |     ...process.env,
  129 |     MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
  130 |     MEDAGENT_EXECUTION_ROOT: path.join(temporary, "records"),
  131 |     MEDAGENT_DESKTOP_LIVE: "1",
  132 |     MEDAGENT_BACKEND: "dsh",
  133 |     MEDAGENT_LLM_BASE_URL: `http://127.0.0.1:${server.address().port}/anthropic`,
  134 |     MEDAGENT_LLM_API_KEY: "native-project-fixture-only",
  135 |     MEDAGENT_LLM_MODEL: "deepseek-flash",
  136 |   };
  137 |   delete environment.ELECTRON_RUN_AS_NODE;
  138 |   const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  139 |   const launch = (env) =>
  140 |     electron.launch({
  141 |       executablePath,
  142 |       args: executablePath ? [] : [root],
  143 |       env,
  144 |     });
  145 |   const picker = async (app, directory) =>
  146 |     app.evaluate(({ dialog }, target) => {
  147 |       dialog.showOpenDialog = async (_window, options) => {
  148 |         if (!options.properties.includes("openDirectory"))
  149 |           throw new Error("directory picker missing");
  150 |         return {
  151 |           canceled: target === null,
  152 |           filePaths: target === null ? [] : [target],
  153 |         };
  154 |       };
  155 |     }, directory);
  156 |   const enter = async (page) => {
  157 |     await page.getByLabel("界面模式").selectOption("developer");
  158 |     await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  159 |   };
  160 |   let app;
  161 |   try {
  162 |     app = await launch(environment);
  163 |     let page = await app.firstWindow();
  164 |     const errors = [];
  165 |     page.on("pageerror", (error) => errors.push(error.message));
  166 |     await enter(page);
  167 |     await picker(app, null);
  168 |     await page.getByRole("button", { name: /打开本地项目/ }).click();
  169 |     await expect(page.locator("#execution-open-project")).toBeEnabled();
  170 |     expect(await page.locator("#execution-workspace option").count()).toBe(1);
  171 |     expect(serial).toBe(0);
  172 |     await picker(app, root);
  173 |     await page.getByRole("button", { name: /打开本地项目/ }).click();
  174 |     await expect(page.locator("#execution-feedback")).toContainText(
  175 |       "项目无法打开",
  176 |     );
  177 |     expect(serial).toBe(0);
  178 |     await picker(app, project);
  179 |     await page.getByRole("button", { name: /打开本地项目/ }).click();
  180 |     await expect(page.locator("#execution-directory")).toContainText(
  181 |       "文件直接保存到原目录",
  182 |     );
  183 |     await expect(page.locator("#execution-directory")).toContainText(project);
  184 |     expect(serial).toBe(0);
  185 |     expect((await readdir(project)).sort()).toEqual([
  186 |       "calculator.py",
  187 |       "keep.txt",
  188 |     ]);
  189 |     const identity = await page.locator("#execution-workspace").inputValue();
  190 |     const rejected = await page.evaluate(
  191 |       async (directory) =>
  192 |         (
  193 |           await fetch("/api/execution", {
  194 |             method: "POST",
  195 |             headers: { "Content-Type": "application/json" },
  196 |             body: JSON.stringify({ directory }),
  197 |           })
  198 |         ).status,
  199 |       project,
  200 |     );
  201 |     expect(rejected).toBe(400);
  202 |     await page
  203 |       .getByLabel("描述执行任务")
  204 |       .fill("ROOT_PROJECT_FIXTURE 修复加法并独立复核。");
  205 |     await page.getByRole("button", { name: "执行任务", exact: true }).click();
  206 |     await expect(page.locator("#execution-status")).toHaveText("本轮已完成", {
  207 |       timeout: 20000,
  208 |     });
  209 |     expect(
  210 |       await readFile(path.join(project, "calculator.py"), "utf8"),
  211 |     ).toContain("return a + b");
  212 |     expect(await readFile(path.join(project, "review.txt"), "utf8")).toBe(
  213 |       "reviewed",
  214 |     );
  215 |     expect(await readFile(path.join(project, "keep.txt"), "utf8")).toBe(
  216 |       "original",
  217 |     );
  218 |     expect(serial).toBe(7);
  219 |     expect(child).toBe(3);
  220 |     expect(issues).toEqual([]);
  221 |     expect(await readdir(project)).not.toContain(".state");
  222 |     expect(await readdir(project)).not.toContain(".index");
  223 |     await page.getByRole("button", { name: /打开本地项目/ }).click();
> 224 |     await expect(page.locator("#execution-workspace")).toHaveValue(identity);
      |                                                        ^ Error: expect(locator).toHaveValue(expected) failed
  225 |     expect(serial).toBe(7);
  226 |     await page.getByRole("button", { name: /查看本轮变更/ }).click();
  227 |     await expect(page.locator("#execution-change-diff")).toContainText(
  228 |       "-    return a - b",
  229 |     );
  230 |     await expect(page.locator("#execution-change-diff")).toContainText(
  231 |       "+    return a + b",
  232 |     );
  233 |     await page.locator("#execution-change-close").click();
  234 |     await app.evaluate(({ BrowserWindow }) => {
  235 |       BrowserWindow.getAllWindows()[0].setSize(900, 800);
  236 |     });
  237 |     await expect
  238 |       .poll(async () =>
  239 |         page.evaluate(
  240 |           () =>
  241 |             document.documentElement.scrollWidth <=
  242 |             document.documentElement.clientWidth,
  243 |         ),
  244 |       )
  245 |       .toBe(true);
  246 |     await expect(page.locator("#execution-open-project")).toBeVisible();
  247 |     await app.evaluate(({ BrowserWindow }) => {
  248 |       BrowserWindow.getAllWindows()[0].setSize(1440, 980);
  249 |     });
  250 |     await page.getByRole("button", { name: /查看本轮变更/ }).click();
  251 |     await mkdir(path.join(root, "docs/assets/ui"), { recursive: true });
  252 |     await page.screenshot({
  253 |       path: path.join(root, "docs/assets/ui/36-local-project-review.png"),
  254 |     });
  255 |     expect(errors).toEqual([]);
  256 |     await app.close();
  257 |     app = null;
  258 |     const offline = { ...environment, MEDAGENT_DESKTOP_LIVE: "0" };
  259 |     for (const key of [
  260 |       "MEDAGENT_LLM_BASE_URL",
  261 |       "MEDAGENT_LLM_API_KEY",
  262 |       "MEDAGENT_LLM_MODEL",
  263 |     ])
  264 |       delete offline[key];
  265 |     app = await launch(offline);
  266 |     page = await app.firstWindow();
  267 |     await enter(page);
  268 |     await picker(app, project);
  269 |     await page.getByRole("button", { name: /打开本地项目/ }).click();
  270 |     await expect(page.locator("#execution-workspace")).toHaveValue(identity);
  271 |     await expect(page.locator("#execution-run")).toBeDisabled();
  272 |     await expect(page.locator("#execution-directory")).toContainText(project);
  273 |     await page.getByRole("button", { name: /查看本轮变更/ }).click();
  274 |     await expect(page.locator("#execution-change-diff")).toContainText(
  275 |       "-    return a - b",
  276 |     );
  277 |     expect(serial).toBe(7);
  278 |   } finally {
  279 |     await app?.close();
  280 |     server.closeAllConnections();
  281 |     await new Promise((resolve) => server.close(resolve));
  282 |     await rm(temporary, { recursive: true, force: true });
  283 |   }
  284 | });
  285 | 
```