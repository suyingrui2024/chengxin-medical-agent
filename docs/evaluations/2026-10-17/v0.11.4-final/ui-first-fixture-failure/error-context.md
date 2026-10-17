# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: present-provenance.spec.mjs >> recorded evidence is inert, malformed sources fall back and old summaries remain readable
- Location: tests/ui/present-provenance.spec.mjs:40:1

# Error details

```
Error: expect(locator).toHaveCount(expected) failed

Locator:  locator('#known-contract img, #known-contract svg, #known-contract script')
Expected: 0
Received: 2
Timeout:  5000ms

Call log:
  - Expect "toHaveCount" locator('#known-contract img, #known-contract svg, #known-contract script') with timeout 5000ms
  - waiting for locator('#known-contract img, #known-contract svg, #known-contract script')
    14 × locator resolved to 2 elements
       - unexpected value "2"

```

# Page snapshot

```yaml
- generic [ref=e1]:
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
        - button "·手臂不舒服" [ref=e29] [cursor=pointer]
        - button "·手臂不舒服" [ref=e30] [cursor=pointer]
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
    - article [ref=e133]:
      - heading "本次问诊摘要" [level=3] [ref=e134]
      - paragraph [ref=e135]: 请核对已记录的信息。“未知”表示尚未确认。
      - generic [ref=e136]:
        - heading "用药与背景" [level=4] [ref=e137]
        - generic [ref=e143]:
          - generic [ref=e144]: 用药与处理
          - generic [ref=e145]:
            - strong [ref=e146]: 已报告
            - group [ref=e147]:
              - generic "原话与来源" [ref=e148] [cursor=pointer]
              - generic [ref=e149]: 患者自述 · 质量未评价
              - blockquote "用药与处理：记录引文" [ref=e150]: <img src=x onerror="window.KNOWN_EXECUTED=1">已报告
              - group [ref=e151]:
                - generic "查看同轮原话" [active] [ref=e152] [cursor=pointer]
                - paragraph [ref=e153]: 含同轮其他信息，尚未全部归入此字段；请以各项最新记录为准。
                - blockquote [ref=e154]: <img src=x onerror="window.KNOWN_EXECUTED=1">已报告，用量不清楚<svg onload="window.KNOWN_EXECUTED=1"></svg>
      - generic [ref=e155]: 合成展示检查
    - article [ref=e157]:
      - heading "本次问诊摘要" [level=3] [ref=e158]
      - paragraph [ref=e159]: 请核对已记录的信息。“未知”表示尚未确认。
      - generic [ref=e160]:
        - heading "用药与背景" [level=4] [ref=e161]
        - generic [ref=e167]:
          - generic [ref=e168]: 用药与处理
          - generic [ref=e169]:
            - strong [ref=e170]: 旧记录
            - group [ref=e171]:
              - generic "来源" [ref=e172] [cursor=pointer]
      - generic [ref=e173]: 合成展示检查
    - status [ref=e175]:
      - strong [ref=e176]: 这部分内容暂时无法显示
      - paragraph [ref=e177]: 展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。
    - status [ref=e179]:
      - strong [ref=e180]: 这部分内容暂时无法显示
      - paragraph [ref=e181]: 展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。
    - status [ref=e183]:
      - strong [ref=e184]: 这部分内容暂时无法显示
      - paragraph [ref=e185]: 展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。
```

# Test source

```ts
  3   |   createPresentProvenanceModel,
  4   |   verifyPresentProvenance,
  5   | } from "../fixtures/present-provenance.mjs";
  6   | 
  7   | for (const mobile of [false, true]) {
  8   |   test(`recorded reports expose scoped original evidence without changing facts${mobile ? " on a narrow screen" : ""}`, async ({
  9   |     page,
  10  |     request,
  11  |   }) => {
  12  |     const model = await createPresentProvenanceModel();
  13  |     try {
  14  |       expect(
  15  |         (
  16  |           await request.post("/api/settings", {
  17  |             data: {
  18  |               backend: "compatible",
  19  |               base_url: model.endpoint,
  20  |               model: "fixture",
  21  |               api_key: "local-provenance",
  22  |             },
  23  |           })
  24  |         ).ok(),
  25  |       ).toBe(true);
  26  |       if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  27  |       await page.goto("/");
  28  |       await verifyPresentProvenance(
  29  |         page,
  30  |         model,
  31  |         `docs/assets/ui/${mobile ? "56-present-provenance-narrow" : "55-present-provenance"}.png`,
  32  |       );
  33  |     } finally {
  34  |       await request.post("/api/settings", { data: { backend: "demo" } });
  35  |       await model.close();
  36  |     }
  37  |   });
  38  | }
  39  | 
  40  | test("recorded evidence is inert, malformed sources fall back and old summaries remain readable", async ({
  41  |   page,
  42  | }) => {
  43  |   await page.goto("/");
  44  |   await page.evaluate(async () => {
  45  |     const { createRenderer } = await import("/structured-ui.js");
  46  |     const renderer = createRenderer();
  47  |     const evidence = '<img src=x onerror="window.KNOWN_EXECUTED=1">已报告';
  48  |     const item = {
  49  |       key: "medications_taken",
  50  |       label: "用药与处理",
  51  |       value: "已报告",
  52  |       evidence,
  53  |       source: "patient",
  54  |       quality: "not_assessed",
  55  |       context: {
  56  |         scope: "source_turn",
  57  |         turn_id: "local-turn",
  58  |         text:
  59  |           evidence + '，用量不清楚<svg onload="window.KNOWN_EXECUTED=1"></svg>',
  60  |       },
  61  |     };
  62  |     const base = {
  63  |       known: [item],
  64  |       unknown: [],
  65  |       care_route: "合成展示检查",
  66  |       return_now_if: [],
  67  |     };
  68  |     const host = document.createElement("div");
  69  |     host.id = "known-contract";
  70  |     host.style.cssText =
  71  |       "position:fixed;inset:0;z-index:100;background:white;overflow:auto";
  72  |     for (const [id, entry] of [
  73  |       ["safe", item],
  74  |       ["old", { key: item.key, label: item.label, value: "旧记录" }],
  75  |       ["bad", { ...item, evidence: {} }],
  76  |       [
  77  |         "mismatch",
  78  |         { ...item, context: { ...item.context, text: "不匹配的原话" } },
  79  |       ],
  80  |       ["wrong-source", { ...item, source: "device" }],
  81  |     ]) {
  82  |       const row = document.createElement("div");
  83  |       row.id = `known-${id}`;
  84  |       row.append(
  85  |         renderer.render(
  86  |           { id, type: "summary", data: { ...base, known: [entry] } },
  87  |           { readOnly: true },
  88  |         ),
  89  |       );
  90  |       host.append(row);
  91  |     }
  92  |     document.body.append(host);
  93  |   });
  94  |   await page.locator("#known-safe .summary-provenance > summary").click();
  95  |   await page.locator("#known-safe .summary-source-context > summary").click();
  96  |   await expect(
  97  |     page.locator("#known-safe .summary-source-context blockquote"),
  98  |   ).toContainText("用量不清楚");
  99  |   await expect(
  100 |     page.locator(
  101 |       "#known-contract img, #known-contract svg, #known-contract script",
  102 |     ),
> 103 |   ).toHaveCount(0);
      |     ^ Error: expect(locator).toHaveCount(expected) failed
  104 |   expect(await page.evaluate(() => window.KNOWN_EXECUTED)).toBeUndefined();
  105 |   await expect(
  106 |     page.locator("#known-contract .structured-fallback"),
  107 |   ).toHaveCount(3);
  108 |   await expect(page.locator("#known-old .summary-item strong")).toHaveText(
  109 |     "旧记录",
  110 |   );
  111 |   await expect(page.locator("#known-contract button")).toHaveCount(0);
  112 | });
  113 | 
```