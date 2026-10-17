# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: freshness.spec.mjs >> native checks mark changed inputs as needing revalidation and accept the actual new check
- Location: tests/desktop/freshness.spec.mjs:13:1

# Error details

```
Error: expect(locator).toHaveText(expected) failed

Locator:  locator('#execution-status')
Expected: "本轮完成"
Received: "本轮已完成"
Timeout:  5000ms

Call log:
  - Expect "toHaveText" locator('#execution-status') with timeout 5000ms
  - waiting for locator('#execution-status')
    6 × locator resolved to <span role="status" aria-live="polite" class="subtle-pill" id="execution-status">正在执行</span>
      - unexpected value "正在执行"
    8 × locator resolved to <span role="status" aria-live="polite" class="subtle-pill" id="execution-status">本轮已完成</span>
      - unexpected value "本轮已完成"

```

```yaml
- status: 本轮已完成
```

# Test source

```ts
  38  |     },
  39  |     {
  40  |       name: "bash",
  41  |       input: {
  42  |         command: "printf '# reviewed change\\n' >> check.py",
  43  |         description: "更新检查依赖文件",
  44  |       },
  45  |     },
  46  |     null,
  47  |     {
  48  |       name: "delivery_check",
  49  |       input: {
  50  |         label: "代码检查",
  51  |         command: "python3 check.py",
  52  |         watch: ["check.py"],
  53  |       },
  54  |     },
  55  |     null,
  56  |   ];
  57  |   const server = createServer(async (request, response) => {
  58  |     const chunks = [];
  59  |     for await (const chunk of request) chunks.push(chunk);
  60  |     requests.push(JSON.parse(Buffer.concat(chunks).toString()));
  61  |     const step = requests.length;
  62  |     if (step === 5) await gate;
  63  |     const tool = tools[step - 1];
  64  |     const block = tool
  65  |       ? { type: "tool_use", id: `freshness-${step}`, ...tool }
  66  |       : {
  67  |           type: "text",
  68  |           text: step === 4 ? "提前结束，尚未复验新文件" : "当前文件已实际复验",
  69  |         };
  70  |     const events = [
  71  |       {
  72  |         type: "message_start",
  73  |         message: {
  74  |           id: `freshness-${step}`,
  75  |           model: "fixture",
  76  |           usage: { input_tokens: 10, output_tokens: 1 },
  77  |         },
  78  |       },
  79  |       { type: "content_block_start", index: 0, content_block: block },
  80  |       { type: "content_block_stop", index: 0 },
  81  |       {
  82  |         type: "message_delta",
  83  |         delta: { stop_reason: tool ? "tool_use" : "end_turn" },
  84  |         usage: { output_tokens: 10 },
  85  |       },
  86  |       { type: "message_stop" },
  87  |     ];
  88  |     response.writeHead(200, { "Content-Type": "text/event-stream" });
  89  |     response.end(
  90  |       events
  91  |         .map(
  92  |           (event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
  93  |         )
  94  |         .join(""),
  95  |     );
  96  |   });
  97  |   server.listen(0, "127.0.0.1");
  98  |   await once(server, "listening");
  99  |   const env = {
  100 |     ...process.env,
  101 |     MEDAGENT_DESKTOP_PROFILE: path.join(temporary, "profile"),
  102 |     MEDAGENT_EXECUTION_ROOT: path.join(temporary, "workspaces"),
  103 |     MEDAGENT_DESKTOP_LIVE: "1",
  104 |     MEDAGENT_BACKEND: "dsh",
  105 |     MEDAGENT_LLM_BASE_URL: `http://127.0.0.1:${server.address().port}/anthropic`,
  106 |     MEDAGENT_LLM_API_KEY: "native-freshness-local-only",
  107 |     MEDAGENT_LLM_MODEL: "fixture",
  108 |   };
  109 |   delete env.ELECTRON_RUN_AS_NODE;
  110 |   const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  111 |   let desktop;
  112 |   try {
  113 |     desktop = await electron.launch({
  114 |       executablePath,
  115 |       args: executablePath ? [] : [root],
  116 |       env,
  117 |     });
  118 |     const page = await desktop.firstWindow();
  119 |     await expect(page.getByText("本机服务已连接")).toBeVisible();
  120 |     await page.getByLabel("界面模式").selectOption("developer");
  121 |     await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  122 |     await page
  123 |       .getByLabel("描述执行任务")
  124 |       .fill("检查代码，更新文件后重新执行检查。改动后的文件需要新的验证结果。");
  125 |     await page.getByRole("button", { name: "执行任务", exact: true }).click();
  126 |     await expect.poll(() => requests.length).toBe(5);
  127 |     const chip = page.locator("#execution-check-summary .execution-check-chip");
  128 |     await expect(chip).toHaveCount(1);
  129 |     await expect(chip).toContainText("需复验");
  130 |     await expect(chip).toHaveClass(/check-unresolved/);
  131 |     await expect(page.locator("#execution-status")).toHaveText("正在执行");
  132 |     await expect(page.locator(".execution-final")).toHaveCount(0);
  133 |     await page.screenshot({
  134 |       path: path.join(root, "docs/assets/ui/45-check-needs-revalidation.png"),
  135 |       fullPage: true,
  136 |     });
  137 |     release();
> 138 |     await expect(page.locator("#execution-status")).toHaveText("本轮完成");
      |                                                     ^ Error: expect(locator).toHaveText(expected) failed
  139 |     await expect(chip).toHaveClass(/check-succeeded/);
  140 |     await expect(chip).toContainText("退出 0");
  141 |     await expect(chip).toHaveAttribute("title", /check.py/);
  142 |     expect(requests.length).toBe(6);
  143 |     await page.screenshot({
  144 |       path: path.join(root, "docs/assets/ui/46-check-revalidated.png"),
  145 |       fullPage: true,
  146 |     });
  147 |   } finally {
  148 |     release();
  149 |     if (desktop) await desktop.close();
  150 |     server.closeAllConnections();
  151 |     await new Promise((resolve) => server.close(resolve));
  152 |     await rm(temporary, { recursive: true, force: true });
  153 |   }
  154 | });
  155 | 
```