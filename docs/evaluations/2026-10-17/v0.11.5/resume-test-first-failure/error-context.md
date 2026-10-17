# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: repeated-observations.spec.mjs >> a narrow-screen repeat returns to the same question with its measurement visible
- Location: tests/ui/repeated-observations.spec.mjs:106:1

# Error details

```
Error: expect(locator).toHaveText(expected) failed

Locator: locator('#active-question')
Timeout: 5000ms
- Expected  - 9
+ Received  + 1

- 和刚开始比，怎么样了？
-
- 选择最接近当前情况的一项。
-
- 在加重
- 在减轻
- 变化不大
- 说不清
- 查看完整问题
+ 和刚开始比，怎么样了？选择最接近当前情况的一项。在加重↗在减轻↗变化不大↗说不清↗查看完整问题从开始到现在，总体是在加重、减轻，还是变化不大？

Call log:
  - Expect "toHaveText" locator('#active-question') with timeout 5000ms
  - waiting for locator('#active-question')
    14 × locator resolved to <div aria-label="当前问题" aria-busy="false" id="active-question" class="active-question" data-identity="4f4582f9-153f-47bf-9760-9506a3f6d689:question:4f4582f9-153f-47bf-9760-9506a3f6d689:5">…</div>
       - unexpected value "和刚开始比，怎么样了？选择最接近当前情况的一项。在加重↗在减轻↗变化不大↗说不清↗查看完整问题从开始到现在，总体是在加重、减轻，还是变化不大？"

```

```yaml
- article:
  - heading "和刚开始比，怎么样了？" [level=2]
  - paragraph: 选择最接近当前情况的一项。
  - button "在加重":
    - strong: 在加重
  - button "在减轻":
    - strong: 在减轻
  - button "变化不大":
    - strong: 变化不大
  - button "说不清":
    - strong: 说不清
  - group: 查看完整问题
```

# Test source

```ts
  40  | test("observation rendering rejects malformed histories and treats evidence as inert text", async ({
  41  |   page,
  42  | }) => {
  43  |   await page.goto("/");
  44  |   await page.evaluate(async () => {
  45  |     const { createRenderer } = await import("/structured-ui.js");
  46  |     const renderer = createRenderer();
  47  |     const series = {
  48  |       task_id: "axillary_temperature",
  49  |       key: "temperature_c",
  50  |       title: "腋温记录",
  51  |       unit: "℃",
  52  |       can_repeat: false,
  53  |       total: 1,
  54  |       latest_status: "recorded",
  55  |       entries: [
  56  |         {
  57  |           id: "one",
  58  |           status: "recorded",
  59  |           value: 37.6,
  60  |           current: true,
  61  |           source: "patient",
  62  |           quality: "not_assessed",
  63  |           recorded_at: "2026-10-17T10:00:00Z",
  64  |           evidence: '<img src=x onerror="window.OBSERVATION_EXECUTED=1">',
  65  |         },
  66  |       ],
  67  |     };
  68  |     const host = document.createElement("div");
  69  |     host.id = "observation-contract";
  70  |     for (const [id, data] of [
  71  |       ["valid", series],
  72  |       [
  73  |         "wrong-type",
  74  |         { ...series, entries: [{ ...series.entries[0], value: "37.6" }] },
  75  |       ],
  76  |       [
  77  |         "duplicate",
  78  |         {
  79  |           ...series,
  80  |           total: 2,
  81  |           entries: [...series.entries, ...series.entries],
  82  |         },
  83  |       ],
  84  |       ["bad-status", { ...series, latest_status: "skipped" }],
  85  |       [
  86  |         "no-current",
  87  |         { ...series, entries: [{ ...series.entries[0], current: false }] },
  88  |       ],
  89  |     ]) {
  90  |       const section = document.createElement("section");
  91  |       section.id = id;
  92  |       section.append(renderer.render({ id, type: "observation_series", data }));
  93  |       host.append(section);
  94  |     }
  95  |     document.body.append(host);
  96  |   });
  97  |   await expect(page.locator("#valid .observation-card")).toHaveCount(1);
  98  |   await expect(page.locator("#valid img")).toHaveCount(0);
  99  |   expect(
  100 |     await page.evaluate(() => window.OBSERVATION_EXECUTED),
  101 |   ).toBeUndefined();
  102 |   for (const id of ["wrong-type", "duplicate", "bad-status", "no-current"])
  103 |     await expect(page.locator(`#${id} .structured-fallback`)).toHaveCount(1);
  104 | });
  105 | 
  106 | test("a narrow-screen repeat returns to the same question with its measurement visible", async ({
  107 |   page,
  108 |   request,
  109 | }) => {
  110 |   const model = await createRepeatedObservationModel();
  111 |   try {
  112 |     await request.post("/api/settings", {
  113 |       data: {
  114 |         backend: "compatible",
  115 |         base_url: model.endpoint,
  116 |         model: "fixture",
  117 |         api_key: "local-observations",
  118 |       },
  119 |     });
  120 |     await page.setViewportSize({ width: 390, height: 844 });
  121 |     await page.goto("/");
  122 |     await expect(page.getByText("本机服务已连接")).toBeVisible();
  123 |     await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  124 |     const send = async (text) => {
  125 |       await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  126 |       await page.getByRole("button", { name: "发送回答" }).click();
  127 |     };
  128 |     await send(
  129 |       "我自己32岁，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。",
  130 |     );
  131 |     await expect(page.locator("#active-task")).toBeVisible();
  132 |     await send("腋温38.25℃");
  133 |     await expect(page.locator("#active-question")).toBeVisible();
  134 |     const question = await page.locator("#active-question").innerText();
  135 |     await send("想重新测温");
  136 |     await expect(
  137 |       page.locator("#active-task .observation-previous"),
  138 |     ).toContainText("38.25 ℃");
  139 |     await send("腋温37.6℃");
> 140 |     await expect(page.locator("#active-question")).toHaveText(question);
      |                                                    ^ Error: expect(locator).toHaveText(expected) failed
  141 |     await expect(
  142 |       page.locator("#active-observation .observation-value"),
  143 |     ).toHaveText("37.6 ℃");
  144 |     await page
  145 |       .locator("#active-observation")
  146 |       .getByRole("button", { name: "再测一次", exact: true })
  147 |       .click();
  148 |     await page.getByRole("button", { name: "暂时跳过", exact: true }).click();
  149 |     await expect(page.locator("#active-question")).toHaveText(question);
  150 |     await expect(page.locator("#active-observation")).toContainText(
  151 |       "上次记录 · 本次未完成",
  152 |     );
  153 |     expect(model.requests).toHaveLength(2);
  154 |     expect(
  155 |       await page.evaluate(
  156 |         () => document.documentElement.scrollWidth <= innerWidth,
  157 |       ),
  158 |     ).toBe(true);
  159 |     await expect(
  160 |       page.getByRole("textbox", { name: "输入你的描述" }),
  161 |     ).toBeInViewport();
  162 |     await page.screenshot({
  163 |       path: "docs/assets/ui/59-repeated-observation-resumed-question.png",
  164 |       fullPage: true,
  165 |     });
  166 |   } finally {
  167 |     await request.post("/api/settings", { data: { backend: "demo" } });
  168 |     await model.close();
  169 |   }
  170 | });
  171 | 
```