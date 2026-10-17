# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: delivery.spec.mjs >> native delivery checks show failure, repair within budget and restore the real check history
- Location: tests/desktop/delivery.spec.mjs:13:1

# Error details

```
Error: locator.fill: Error: strict mode violation: getByLabel('执行任务') resolved to 2 elements:
    1) <section aria-label="执行任务" class="surface execution-main">…</section> aka getByRole('region', { name: '执行任务' })
    2) <textarea rows="3" maxlength="16000" id="execution-input" placeholder="例如：创建一个单位换算脚本，编写并运行测试，把结果保存到报告中。"></textarea> aka getByRole('textbox', { name: '描述执行任务' })

Call log:
  - waiting for getByLabel('执行任务')

```

# Test source

```ts
  22  |     {
  23  |       name: "bash",
  24  |       input: {
  25  |         command: "printf 'assert 2 + 2 == 5\\n' > check.py",
  26  |         description: "Create an incorrect test expectation",
  27  |       },
  28  |     },
  29  |     {
  30  |       name: "delivery_check",
  31  |       input: { label: "新增测试", command: "python3 check.py" },
  32  |     },
  33  |     null,
  34  |     {
  35  |       name: "bash",
  36  |       input: {
  37  |         command: "printf 'assert 2 + 2 == 4\\n' > check.py",
  38  |         description: "Correct the expectation using actual arithmetic",
  39  |       },
  40  |     },
  41  |     {
  42  |       name: "delivery_check",
  43  |       input: { label: "新增测试", command: "python3 check.py" },
  44  |     },
  45  |     null,
  46  |   ];
  47  |   const server = createServer(async (request, response) => {
  48  |     const chunks = [];
  49  |     for await (const chunk of request) chunks.push(chunk);
  50  |     const body = JSON.parse(Buffer.concat(chunks).toString());
  51  |     requests.push(body);
  52  |     const step = requests.length;
  53  |     if (step === 4) await gate;
  54  |     const tool = tools[step - 1];
  55  |     const content = tool
  56  |       ? { type: "tool_use", id: `delivery-${step}`, ...tool }
  57  |       : {
  58  |           type: "text",
  59  |           text: step === 3 ? "错误的提前完成" : "修正并复验完成",
  60  |         };
  61  |     const events = [
  62  |       {
  63  |         type: "message_start",
  64  |         message: {
  65  |           id: `delivery-${step}`,
  66  |           model: "fixture",
  67  |           usage: { input_tokens: 10, output_tokens: 1 },
  68  |         },
  69  |       },
  70  |       { type: "content_block_start", index: 0, content_block: content },
  71  |       { type: "content_block_stop", index: 0 },
  72  |       {
  73  |         type: "message_delta",
  74  |         delta: { stop_reason: tool ? "tool_use" : "end_turn" },
  75  |         usage: { output_tokens: 10 },
  76  |       },
  77  |       { type: "message_stop" },
  78  |     ];
  79  |     response.writeHead(200, { "Content-Type": "text/event-stream" });
  80  |     response.end(
  81  |       events
  82  |         .map(
  83  |           (event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
  84  |         )
  85  |         .join(""),
  86  |     );
  87  |   });
  88  |   server.listen(0, "127.0.0.1");
  89  |   await once(server, "listening");
  90  |   const endpoint = `http://127.0.0.1:${server.address().port}/anthropic`;
  91  |   const env = {
  92  |     ...process.env,
  93  |     MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
  94  |     MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
  95  |     MEDAGENT_DESKTOP_LIVE: "1",
  96  |     MEDAGENT_BACKEND: "dsh",
  97  |     MEDAGENT_LLM_BASE_URL: endpoint,
  98  |     MEDAGENT_LLM_API_KEY: "native-delivery-local-only",
  99  |     MEDAGENT_LLM_MODEL: "fixture",
  100 |   };
  101 |   delete env.ELECTRON_RUN_AS_NODE;
  102 |   const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  103 |   const launch = (environment) =>
  104 |     electron.launch({
  105 |       executablePath,
  106 |       args: executablePath ? [] : [root],
  107 |       env: environment,
  108 |     });
  109 |   let desktop;
  110 |   try {
  111 |     desktop = await launch(env);
  112 |     let page = await desktop.firstWindow();
  113 |     await expect(page.getByText("本机服务已连接")).toBeVisible();
  114 |     const settings = await page.evaluate(async () =>
  115 |       (await fetch("/api/settings")).json(),
  116 |     );
  117 |     expect(settings.base_url).toBe(endpoint);
  118 |     await page.getByLabel("界面模式").selectOption("developer");
  119 |     await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  120 |     await page
  121 |       .getByLabel("执行任务")
> 122 |       .fill("创建并实际检查错误预期，按工具结果修正并复验。");
      |        ^ Error: locator.fill: Error: strict mode violation: getByLabel('执行任务') resolved to 2 elements:
  123 |     await page.getByRole("button", { name: "开始执行", exact: true }).click();
  124 |     await expect.poll(() => requests.length).toBe(4);
  125 |     await expect(page.locator("#execution-status")).toHaveText("正在执行");
  126 |     await expect(page.locator("#execution-results")).toContainText(
  127 |       "正在修正交付检查",
  128 |     );
  129 |     await expect(page.locator(".execution-final")).toHaveCount(0);
  130 |     let chip = page.locator("#execution-check-summary .execution-check-chip");
  131 |     await expect(chip).toHaveCount(1);
  132 |     await expect(chip).toContainText("退出 1");
  133 |     await chip.focus();
  134 |     for (let index = 0; index < 2; index++) {
  135 |       const response = await page.waitForResponse((response) =>
  136 |         /\/api\/execution\/[^/?]+\?after=/.test(response.url()),
  137 |       );
  138 |       await response.finished();
  139 |       await page.evaluate(
  140 |         () =>
  141 |           new Promise((resolve) =>
  142 |             requestAnimationFrame(() => requestAnimationFrame(resolve)),
  143 |           ),
  144 |       );
  145 |       await expect(chip).toBeFocused();
  146 |     }
  147 |     await page.screenshot({
  148 |       path: "docs/assets/ui/43-delivery-check-repair.png",
  149 |       animations: "disabled",
  150 |     });
  151 |     release();
  152 |     await expect(page.locator("#execution-status")).toHaveText("本轮已完成");
  153 |     await expect(chip).toContainText("退出 0");
  154 |     await expect(chip).toHaveClass(/check-succeeded/);
  155 |     await expect(page.locator(".execution-final")).toHaveCount(1);
  156 |     expect(requests).toHaveLength(6);
  157 |     expect(JSON.stringify(requests[2])).toContain("AssertionError");
  158 |     expect(JSON.stringify(requests[3])).toContain("交付检查纠错");
  159 |     await chip.click();
  160 |     await expect(
  161 |       page.locator(".execution-delivery details[open]").last(),
  162 |     ).toContainText("新增测试");
  163 |     const identity = path.basename(
  164 |       await page.locator("#execution-directory").textContent(),
  165 |     );
  166 |     await page
  167 |       .locator("#execution-files .execution-file")
  168 |       .filter({ hasText: "check.py" })
  169 |       .click();
  170 |     await expect(page.locator("#execution-preview")).toContainText(
  171 |       "assert 2 + 2 == 4",
  172 |     );
  173 |     await page.screenshot({
  174 |       path: "docs/assets/ui/44-delivery-check-result.png",
  175 |       animations: "disabled",
  176 |     });
  177 |     await expect(page.locator("#execution-close")).toBeEnabled();
  178 |     await page.locator("#execution-close").click();
  179 |     await expect(page.locator("#execution-check-summary")).toBeHidden();
  180 |     await desktop.close();
  181 |     desktop = null;
  182 |     const offline = { ...env, MEDAGENT_DESKTOP_LIVE: "0" };
  183 |     for (const key of [
  184 |       "MEDAGENT_LLM_BASE_URL",
  185 |       "MEDAGENT_LLM_API_KEY",
  186 |       "MEDAGENT_LLM_MODEL",
  187 |     ])
  188 |       delete offline[key];
  189 |     desktop = await launch(offline);
  190 |     page = await desktop.firstWindow();
  191 |     await page.getByLabel("界面模式").selectOption("developer");
  192 |     await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  193 |     await page.getByLabel("选择执行工作区").selectOption(identity);
  194 |     chip = page.locator("#execution-check-summary .execution-check-chip");
  195 |     await expect(chip).toContainText("退出 0");
  196 |     await chip.click();
  197 |     await expect(
  198 |       page.locator(".execution-delivery details[open]").last(),
  199 |     ).toContainText("已保存的运行观察");
  200 |     await expect(page.locator(".execution-delivery")).toHaveCount(2);
  201 |     expect(requests).toHaveLength(6);
  202 |   } finally {
  203 |     release();
  204 |     await desktop?.close();
  205 |     await new Promise((resolve) => server.close(resolve));
  206 |     await rm(temporary, { recursive: true, force: true });
  207 |   }
  208 | });
  209 | 
```