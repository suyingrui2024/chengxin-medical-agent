# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: repeated-observations.spec.mjs >> observation rendering rejects malformed histories and treats evidence as inert text
- Location: tests/ui/repeated-observations.spec.mjs:40:1

# Error details

```
Error: expect(locator).toHaveCount(expected) failed

Locator:  locator('#wrong-type .unsupported-block')
Expected: 1
Received: 0
Timeout:  5000ms

Call log:
  - Expect "toHaveCount" locator('#wrong-type .unsupported-block') with timeout 5000ms
  - waiting for locator('#wrong-type .unsupported-block')
    14 × locator resolved to 0 elements
       - unexpected value "0"

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - link "跳到主要内容" [ref=e2] [cursor=pointer]:
    - /url: "#main"
  - generic [ref=e3]:
    - complementary "主导航" [ref=e4]:
      - link "澄心 CHENGXIN HEALTH" [ref=e5] [cursor=pointer]:
        - /url: "#consultation"
        - generic [ref=e6]:
          - strong [ref=e7]: 澄心
          - generic [ref=e8]: CHENGXIN HEALTH
      - button "新的问诊 ↗" [ref=e9] [cursor=pointer]:
        - text: 新的问诊
        - generic [ref=e12]: ↗
      - generic [ref=e13]:
        - text: 界面模式
        - combobox "界面模式" [ref=e14]:
          - option "患者视图" [selected]
          - option "研发视图"
      - paragraph [ref=e15]: 工作空间
      - navigation [ref=e16]:
        - button "问诊" [ref=e17] [cursor=pointer]
        - button "会谈记录 2" [ref=e21] [cursor=pointer]:
          - text: 会谈记录
          - generic [ref=e24]: "2"
      - generic [ref=e25]:
        - generic [ref=e26]: 最近问诊
        - generic [ref=e27]: "2"
      - generic [ref=e28]:
        - button "·咳嗽发烧" [ref=e29] [cursor=pointer]
        - button "·咳嗽发烧" [ref=e30] [cursor=pointer]
      - generic [ref=e31]:
        - generic [ref=e34]:
          - text: 本机工作空间
          - generic [ref=e35]: 会谈仅保留到服务关闭
        - generic [ref=e36]:
          - generic [ref=e37]: 研究原型
          - generic [ref=e38]: 能力协议 v1
    - generic [ref=e39]:
      - banner [ref=e40]:
        - generic [ref=e41]:
          - generic [ref=e42]: 工作空间
          - generic [ref=e43]: /
          - strong [ref=e44]: 问诊
        - generic [ref=e45]:
          - generic [ref=e46]: 本机服务已连接
          - generic [ref=e48]: 离线演示
      - main [ref=e49]:
        - region "问诊" [ref=e50]:
          - generic [ref=e51]:
            - generic [ref=e52]:
              - paragraph [ref=e53]: A LITTLE CLARITY, ONE STEP AT A TIME
              - heading "先说说，哪里不舒服？" [level=1] [ref=e54]
              - paragraph [ref=e55]: 从你的描述开始，一起把情况理清楚。
            - button "问诊记录 0" [ref=e56] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e59]: "0"
          - generic [ref=e60]:
            - region "问诊对话" [ref=e61]:
              - generic [ref=e62]:
                - generic [ref=e65]:
                  - strong [ref=e66]: 澄心问诊助手
                  - generic [ref=e67]: 倾听 · 澄清 · 核对
                - generic [ref=e68]: 准备开始
              - generic [ref=e71]:
                - generic [aria-hidden] [ref=e72]:
                  - generic [ref=e75]: 一步一步
                  - generic [ref=e76]: 清楚一点
                - heading "不用一次说完整。" [level=2] [ref=e79]
                - paragraph [ref=e80]: 你可以按自己的话描述。我们会逐步追问，必要时用图示帮助你记录和观察。
                - button "开始问诊" [ref=e81] [cursor=pointer]
                - generic [ref=e84]: 也可以从一个问题开始
                - generic [ref=e86]:
                  - button "01 最近有些不舒服 描述症状和变化 ↗" [ref=e87] [cursor=pointer]:
                    - generic [ref=e88]: "01"
                    - strong [ref=e89]: 最近有些不舒服
                    - generic [ref=e90]:
                      - text: 描述症状和变化
                      - generic [ref=e91]: ↗
                  - button "02 替家人问一问 一起梳理现有信息 ↗" [ref=e92] [cursor=pointer]:
                    - generic [ref=e93]: "02"
                    - strong [ref=e94]: 替家人问一问
                    - generic [ref=e95]:
                      - text: 一起梳理现有信息
                      - generic [ref=e96]: ↗
              - generic [ref=e97]:
                - generic "快捷回答"
                - generic [ref=e98]: 开始后，你可以随时补充或更正信息。
                - generic [ref=e99]:
                  - generic [ref=e100]: 输入你的描述
                  - textbox "输入你的描述" [ref=e101]:
                    - /placeholder: 按自己的话描述，不用担心遗漏…
                  - button "发送回答" [ref=e102] [cursor=pointer]
                - paragraph [ref=e105]:
                  - generic [ref=e106]: 0 / 2000
            - complementary "问诊记录" [ref=e107]:
              - generic [ref=e108]:
                - generic [ref=e109]: 本次问诊
                - generic [ref=e110]: 尚未开始
              - strong [ref=e112]: 先了解最困扰你的问题
              - generic [ref=e113]:
                - heading "已记录的信息" [level=2] [ref=e114]
                - generic [ref=e115]: 0 项
              - paragraph [ref=e120]: 每一步的信息，会在这里逐渐变得清晰。
              - generic [ref=e121]:
                - generic [ref=e122]: 已提供
                - generic [ref=e124]: 待确认 / 未知
              - paragraph [ref=e126]: 记录可以更正。没有提到的信息，不会自动记为“没有”。
          - generic [ref=e127]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
  - generic [ref=e131]:
    - generic [ref=e133]:
      - generic [ref=e134]: 腋温记录
      - generic [ref=e136]: 最新记录
      - strong [ref=e137]: 37.6 ℃
      - paragraph [ref=e138]: 患者自述 · 质量未评价
      - group [ref=e139]:
        - generic "查看记录 · 1条" [ref=e140] [cursor=pointer]
    - status [ref=e142]:
      - strong [ref=e143]: 这部分内容暂时无法显示
      - paragraph [ref=e144]: 展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。
    - status [ref=e146]:
      - strong [ref=e147]: 这部分内容暂时无法显示
      - paragraph [ref=e148]: 展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。
    - status [ref=e150]:
      - strong [ref=e151]: 这部分内容暂时无法显示
      - paragraph [ref=e152]: 展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。
    - status [ref=e154]:
      - strong [ref=e155]: 这部分内容暂时无法显示
      - paragraph [ref=e156]: 展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。
```

# Test source

```ts
  3   |   createRepeatedObservationModel,
  4   |   verifyRepeatedObservations,
  5   | } from "../fixtures/repeated-observations.mjs";
  6   | 
  7   | for (const mobile of [false, true]) {
  8   |   test(`repeated temperatures retain history, distinguish skipped attempts and reassess risk${mobile ? " on a narrow screen" : ""}`, async ({
  9   |     page,
  10  |     request,
  11  |   }) => {
  12  |     const model = await createRepeatedObservationModel();
  13  |     try {
  14  |       expect(
  15  |         (
  16  |           await request.post("/api/settings", {
  17  |             data: {
  18  |               backend: "compatible",
  19  |               base_url: model.endpoint,
  20  |               model: "fixture",
  21  |               api_key: "local-observations",
  22  |             },
  23  |           })
  24  |         ).ok(),
  25  |       ).toBe(true);
  26  |       if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  27  |       await page.goto("/");
  28  |       await verifyRepeatedObservations(
  29  |         page,
  30  |         model,
  31  |         `docs/assets/ui/${mobile ? "58-repeated-observation-narrow" : "57-repeated-observation"}`,
  32  |       );
  33  |     } finally {
  34  |       await request.post("/api/settings", { data: { backend: "demo" } });
  35  |       await model.close();
  36  |     }
  37  |   });
  38  | }
  39  | 
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
> 103 |     await expect(page.locator(`#${id} .structured-fallback`)).toHaveCount(1);
      |                                                             ^ Error: expect(locator).toHaveCount(expected) failed
  104 | });
  105 | 
```