# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: app.spec.mjs >> desktop app owns its window, private sidecar and shutdown lifecycle
- Location: tests/desktop/app.spec.mjs:59:1

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('#capability-test-result')
Expected substring: "1250 g"
Received string:    "血糖单位换算已计算 · 输入待核对≈ 180 mg/dL输入数值10原单位mmol/L目标单位mg/dL10 mmol/L × 18 ≈ 180 mg/dL按ADA说明的血糖常用约定系数18换算，使用Decimal十进制运算；该系数为近似约定。输入待核对 · 质量未评价。结果未写为临床事实。执行依据与限制calculate.glucose @ 0.1.0来源：研发独立输入能力组合：dfee9fb48ac7a6d946b4ae7a90d85dfb437f596a5105193981729f9ee26a0dee仅换算血糖（葡萄糖）读数的单位，不适用于其他化验项目或食物糖分。不核验测量设备、采样条件或读数真实性；不判断高低，不推算饮食、诊断或药物剂量。同单位保留输入值；跨单位显示最多4位小数，ROUND_HALF_UP；非零极小值改保留4位有效数字以免显示为零。显示位数不代表测量精度。公式来源American Diabetes Association · Common TermsMg/dL与Mmol/L词条的文字换算规则；采用常用近似系数18。 · 核对日期 2026-10-17"
Timeout: 5000ms

Call log:
  - Expect "toContainText" locator('#capability-test-result') with timeout 5000ms
  - waiting for locator('#capability-test-result')
    14 × locator resolved to <div aria-live="polite" id="capability-test-result">…</div>
       - unexpected value "血糖单位换算已计算 · 输入待核对≈ 180 mg/dL输入数值10原单位mmol/L目标单位mg/dL10 mmol/L × 18 ≈ 180 mg/dL按ADA说明的血糖常用约定系数18换算，使用Decimal十进制运算；该系数为近似约定。输入待核对 · 质量未评价。结果未写为临床事实。执行依据与限制calculate.glucose @ 0.1.0来源：研发独立输入能力组合：dfee9fb48ac7a6d946b4ae7a90d85dfb437f596a5105193981729f9ee26a0dee仅换算血糖（葡萄糖）读数的单位，不适用于其他化验项目或食物糖分。不核验测量设备、采样条件或读数真实性；不判断高低，不推算饮食、诊断或药物剂量。同单位保留输入值；跨单位显示最多4位小数，ROUND_HALF_UP；非零极小值改保留4位有效数字以免显示为零。显示位数不代表测量精度。公式来源American Diabetes Association · Common TermsMg/dL与Mmol/L词条的文字换算规则；采用常用近似系数18。 · 核对日期 2026-10-17"

```

```yaml
- article:
  - paragraph: 血糖单位换算
  - text: 已计算 · 输入待核对
  - heading "≈ 180 mg/dL" [level=3]
  - term: 输入数值
  - definition: "10"
  - term: 原单位
  - definition: mmol/L
  - term: 目标单位
  - definition: mg/dL
  - paragraph: 10 mmol/L × 18 ≈ 180 mg/dL
  - paragraph: 按ADA说明的血糖常用约定系数18换算，使用Decimal十进制运算；该系数为近似约定。
  - paragraph: 输入待核对 · 质量未评价。结果未写为临床事实。
  - group: 执行依据与限制
```

# Test source

```ts
  30  |     await expect(window.locator("#backend-pill")).toContainText("直接模型");
  31  |     const result = await window.evaluate(async () => {
  32  |       const settings = await (await fetch("/api/settings")).json();
  33  |       const created = await (
  34  |         await fetch("/api/sessions", {
  35  |           method: "POST",
  36  |           headers: { "Content-Type": "application/json" },
  37  |           body: "{}",
  38  |         })
  39  |       ).json();
  40  |       const trace = await (
  41  |         await fetch(`/api/sessions/${created.session_id}/events`)
  42  |       ).json();
  43  |       return {
  44  |         settings,
  45  |         modelCalls: trace.events.filter(
  46  |           (event) => event.event_type === "model.requested",
  47  |         ).length,
  48  |       };
  49  |     });
  50  |     expect(result.settings.backend).toBe("compatible");
  51  |     expect(result.settings.has_api_key).toBe(true);
  52  |     expect(result.modelCalls).toBe(0);
  53  |     expect(JSON.stringify(result)).not.toContain("desktop-test-only-secret");
  54  |   } finally {
  55  |     await desktop.close();
  56  |   }
  57  | });
  58  | 
  59  | test("desktop app owns its window, private sidecar and shutdown lifecycle", async () => {
  60  |   const environment = { ...process.env };
  61  |   delete environment.ELECTRON_RUN_AS_NODE;
  62  |   environment.MEDAGENT_DESKTOP_PROFILE = path.join(
  63  |     root,
  64  |     ".runtime",
  65  |     "desktop-test",
  66  |   );
  67  |   const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  68  |   const desktop = await electron.launch({
  69  |     executablePath,
  70  |     args: executablePath ? [] : [root],
  71  |     env: environment,
  72  |   });
  73  |   let origin;
  74  |   try {
  75  |     const window = await desktop.firstWindow();
  76  |     await expect(
  77  |       window.getByRole("button", { name: "开始问诊", exact: true }),
  78  |     ).toBeVisible();
  79  |     origin = new URL(window.url()).origin;
  80  |     await expect(window.getByText("本机服务已连接")).toBeVisible();
  81  |     const preferences = await desktop.evaluate(({ BrowserWindow }) => {
  82  |       const win = BrowserWindow.getAllWindows()[0];
  83  |       const prefs = win.webContents.getLastWebPreferences();
  84  |       return {
  85  |         title: win.getTitle(),
  86  |         sandbox: prefs.sandbox,
  87  |         nodeIntegration: prefs.nodeIntegration,
  88  |         contextIsolation: prefs.contextIsolation,
  89  |       };
  90  |     });
  91  |     expect(preferences).toEqual({
  92  |       title: "澄心问诊",
  93  |       sandbox: true,
  94  |       nodeIntegration: false,
  95  |       contextIsolation: true,
  96  |     });
  97  |     // Requests from ordinary browsers/HTTP clients cannot use this desktop instance.
  98  |     expect((await fetch(`${origin}/api/sessions`)).status).toBe(401);
  99  |     expect((await fetch(`${origin}/`)).status).toBe(401);
  100 |     expect(await window.evaluate(() => typeof window.require)).toBe(
  101 |       "undefined",
  102 |     );
  103 |     await mkdir(path.join(root, "docs/assets/ui"), { recursive: true });
  104 |     await window.screenshot({
  105 |       path: path.join(root, "docs/assets/ui/07-desktop-app.png"),
  106 |     });
  107 |     await window.getByRole("button", { name: "开始问诊", exact: true }).click();
  108 |     await expect(window.locator("#active-question .visual-choice")).toHaveCount(
  109 |       3,
  110 |     );
  111 |     await window.screenshot({
  112 |       path: path.join(root, "docs/assets/ui/21-desktop-visual-question.png"),
  113 |     });
  114 |     await window.getByRole("button", { name: "我自己", exact: true }).click();
  115 |     await window.getByRole("textbox", { name: "输入你的描述" }).fill("32岁");
  116 |     await window.getByRole("button", { name: "发送回答" }).click();
  117 |     await expect(
  118 |       window.locator(".fact-row").filter({ hasText: "年龄" }),
  119 |     ).toContainText("32 岁");
  120 |     await window.getByLabel("界面模式").selectOption("developer");
  121 |     await window.getByRole("button", { name: "模型与运行配置" }).click();
  122 |     await expect(
  123 |       window.getByRole("dialog", { name: "模型与运行配置" }),
  124 |     ).toBeVisible();
  125 |     await window.getByRole("button", { name: "测试连接" }).click();
  126 |     await expect(window.locator("#settings-feedback")).toContainText("已就绪");
  127 |     await window.getByRole("button", { name: "关闭配置" }).click();
  128 |     await window.locator('.nav-item[data-view="capabilities"]').click();
  129 |     await window.getByRole("button", { name: "执行并查看公式" }).click();
> 130 |     await expect(window.locator("#capability-test-result")).toContainText(
      |                                                             ^ Error: expect(locator).toContainText(expected) failed
  131 |       "1250 g",
  132 |     );
  133 |     await window.locator('.nav-item[data-view="tasks"]').click();
  134 |     await window
  135 |       .getByRole("button", { name: "查看图示与步骤" })
  136 |       .first()
  137 |       .click();
  138 |     await expect(
  139 |       window.getByRole("dialog", { name: "图示任务预览" }),
  140 |     ).toBeVisible();
  141 |     await window.getByRole("button", { name: "下一步", exact: true }).click();
  142 |     await expect(window.locator("#task-preview .task-image")).toHaveAttribute(
  143 |       "src",
  144 |       "/temperature.svg#step-2",
  145 |     );
  146 |     await window.screenshot({
  147 |       path: path.join(root, "docs/assets/ui/11-desktop-structured.png"),
  148 |     });
  149 |     await window.getByRole("button", { name: "关闭预览" }).click();
  150 |     await window.locator('.nav-item[data-view="inspect"]').click();
  151 |     const exportPath = path.join(
  152 |       root,
  153 |       "test-results/desktop/synthetic-native-export.json",
  154 |     );
  155 |     await desktop.evaluate(({ BrowserWindow }, target) => {
  156 |       globalThis.medagentEvaluationDownload = "waiting";
  157 |       BrowserWindow.getAllWindows()[0].webContents.session.once(
  158 |         "will-download",
  159 |         (_event, item) => {
  160 |           item.setSavePath(target);
  161 |           item.once("done", (_doneEvent, state) => {
  162 |             globalThis.medagentEvaluationDownload = state;
  163 |           });
  164 |         },
  165 |       );
  166 |     }, exportPath);
  167 |     await window.getByRole("button", { name: "导出本次记录" }).click();
  168 |     await expect
  169 |       .poll(
  170 |         () => desktop.evaluate(() => globalThis.medagentEvaluationDownload),
  171 |         { timeout: 10000 },
  172 |       )
  173 |       .toBe("completed");
  174 |     const exported = JSON.parse(await readFile(exportPath, "utf8"));
  175 |     expect(exported.case.facts.age_years.value).toBe(32);
  176 |     expect(exported.events.length).toBeGreaterThan(0);
  177 |   } finally {
  178 |     await desktop.close();
  179 |   }
  180 |   await expect
  181 |     .poll(async () => {
  182 |       try {
  183 |         await fetch(`${origin}/api/health`);
  184 |         return false;
  185 |       } catch {
  186 |         return true;
  187 |       }
  188 |     })
  189 |     .toBe(true);
  190 | });
  191 | 
```