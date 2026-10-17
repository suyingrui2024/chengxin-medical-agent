# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: measurement-supplements.spec.mjs >> full measurement reports retain medication updates through failure and retry on a narrow screen
- Location: tests/ui/measurement-supplements.spec.mjs:8:3

# Error details

```
Error: Playwright Test did not expect test() to be called here.
Most common reasons include:
- You are calling test() in a configuration file.
- You are calling test() in a file that is imported by the configuration file.
- You have two different versions of @playwright/test. This usually happens
  when one of the dependencies in your package.json depends on @playwright/test.
- You are calling test() from an async test.describe() block. Only sync ones are supported.
```

# Page snapshot

```yaml
- generic [ref=e1]:
  - link "跳到主要内容" [ref=e2] [cursor=pointer]:
    - /url: "#main"
  - generic [ref=e3]:
    - text: · ·
    - generic [ref=e4]:
      - banner [ref=e5]:
        - generic [ref=e6]:
          - button "打开导航" [ref=e7] [cursor=pointer]
          - strong [ref=e10]: 问诊
        - generic [ref=e11]:
          - generic [ref=e12]: 本机服务已连接
          - generic [ref=e14]: 直接模型
      - main [ref=e15]:
        - region "问诊" [ref=e16]:
          - generic [ref=e17]:
            - heading "核对本次记录" [level=1] [ref=e19]
            - button "问诊记录 16" [ref=e20] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e21]: "16"
          - region "问诊对话" [ref=e23]:
            - generic [ref=e24]:
              - generic [ref=e27]:
                - strong [ref=e28]: 澄心问诊助手
                - generic [ref=e29]: 直接模型 · 本次会谈
              - button "紧急求助" [ref=e31] [cursor=pointer]
            - generic [ref=e34]:
              - status [ref=e35]: 核对记录 · 可以更正
              - button "展开对话记录" [ref=e36] [cursor=pointer]
            - log "会谈消息" [ref=e38]:
              - article [ref=e39]:
                - heading "本次问诊摘要" [level=3] [ref=e40]
                - paragraph [ref=e41]: 请核对已记录的信息。“未知”表示尚未确认。
                - generic [ref=e42]:
                  - generic [ref=e43]:
                    - generic [ref=e44]: 腋温记录
                    - button "再测一次" [ref=e45] [cursor=pointer]
                  - generic [ref=e46]: 上次记录 · 本次未完成
                  - strong [ref=e47]: 37.6 ℃
                  - paragraph [ref=e48]: 患者自述 · 质量未评价
                  - group [ref=e49]:
                    - generic "查看记录 · 3条" [ref=e50] [cursor=pointer]
                  - group [ref=e51]:
                    - generic "补充原话 · 已带入本轮问诊" [active] [ref=e52] [cursor=pointer]
                    - blockquote [ref=e53]: 腋温38.6℃，读数可能不准；更正：今天用过药物乙，用量不清楚。
                - generic [ref=e54]:
                  - generic [ref=e55]:
                    - generic [ref=e56]: 年龄
                    - generic [ref=e60]:
                      - strong [ref=e61]: 32 岁
                      - group [ref=e62]:
                        - generic "原话与来源" [ref=e63] [cursor=pointer]
                    - button "更正" [ref=e64] [cursor=pointer]
                  - generic [ref=e65]:
                    - generic [ref=e66]: 影响程度
                    - generic [ref=e70]:
                      - strong [ref=e71]: 3 / 10
                      - group [ref=e72]:
                        - generic "原话与来源" [ref=e73] [cursor=pointer]
                    - button "更正" [ref=e74] [cursor=pointer]
                - generic [ref=e75]:
                  - strong [ref=e76]: 仍待确认
                  - generic [ref=e78]:
                    - generic [ref=e79]:
                      - strong [ref=e80]: 过敏信息
                      - button "补充 / 更正" [ref=e81] [cursor=pointer]
                    - blockquote "过敏信息：原话，待确认" [ref=e82]: 过敏不知道
                    - text: 患者自述 · 尚未确认
                    - group [ref=e83]:
                      - generic "查看同轮原话" [ref=e84]
                - generic [ref=e85]:
                  - heading "当前不适" [level=4] [ref=e86]
                  - generic [ref=e91]:
                    - generic [ref=e92]:
                      - generic [ref=e93]: 主要问题
                      - generic [ref=e94]:
                        - strong [ref=e95]: 咳嗽发烧
                        - group [ref=e96]:
                          - generic "原话与来源" [ref=e97] [cursor=pointer]
                      - button "更正" [ref=e98] [cursor=pointer]
                    - generic [ref=e99]:
                      - generic [ref=e100]: 开始与起病方式
                      - generic [ref=e101]:
                        - strong [ref=e102]: 两天
                        - group [ref=e103]:
                          - generic "原话与来源" [ref=e104] [cursor=pointer]
                      - button "更正" [ref=e105] [cursor=pointer]
                    - generic [ref=e106]:
                      - generic [ref=e107]: 急症筛查
                      - generic [ref=e108]:
                        - strong [ref=e109]: 患者否认上述危险信号
                        - group [ref=e110]:
                          - generic "原话与来源" [ref=e111] [cursor=pointer]
                    - generic [ref=e112]:
                      - generic [ref=e113]: 变化趋势
                      - generic [ref=e114]:
                        - strong [ref=e115]: 变化不大
                        - group [ref=e116]:
                          - generic "原话与来源" [ref=e117] [cursor=pointer]
                      - button "更正" [ref=e118] [cursor=pointer]
                    - generic [ref=e119]:
                      - generic [ref=e120]: 伴随不适
                      - generic [ref=e121]:
                        - strong [ref=e122]: 否认/没有
                        - group [ref=e123]:
                          - generic "原话与来源" [ref=e124] [cursor=pointer]
                      - button "更正" [ref=e125] [cursor=pointer]
                    - generic [ref=e126]:
                      - generic [ref=e127]: 呼吸情况
                      - generic [ref=e128]:
                        - strong [ref=e129]: 没有气促，能完整说话
                        - group [ref=e130]:
                          - generic "原话与来源" [ref=e131] [cursor=pointer]
                      - button "更正" [ref=e132] [cursor=pointer]
                    - generic [ref=e133]:
                      - generic [ref=e134]: 咳嗽细节
                      - generic [ref=e135]:
                        - strong [ref=e136]: 干咳，没有咳血
                        - group [ref=e137]:
                          - generic "原话与来源" [ref=e138] [cursor=pointer]
                      - button "更正" [ref=e139] [cursor=pointer]
                    - generic [ref=e140]:
                      - generic [ref=e141]: 饮水进食与排尿
                      - generic [ref=e142]:
                        - strong [ref=e143]: 能正常喝水吃饭，排尿正常
                        - group [ref=e144]:
                          - generic "原话与来源" [ref=e145] [cursor=pointer]
                      - button "更正" [ref=e146] [cursor=pointer]
                - generic [ref=e147]:
                  - heading "用药与背景" [level=4] [ref=e148]
                  - generic [ref=e153]:
                    - generic [ref=e154]:
                      - generic [ref=e155]: 咨询对象
                      - generic [ref=e156]:
                        - strong [ref=e157]: 本人
                        - group [ref=e158]:
                          - generic "原话与来源" [ref=e159] [cursor=pointer]
                      - button "更正" [ref=e160] [cursor=pointer]
                    - generic [ref=e161]:
                      - generic [ref=e162]: 已做处理/用药
                      - generic [ref=e163]:
                        - strong [ref=e164]: 今天用过药物乙，用量不清楚
                        - group [ref=e165]:
                          - generic "原话与来源" [ref=e166] [cursor=pointer]
                      - button "更正" [ref=e167] [cursor=pointer]
                    - generic [ref=e168]:
                      - generic [ref=e169]: 相关病史
                      - generic [ref=e170]:
                        - strong [ref=e171]: 否认/没有
                        - group [ref=e172]:
                          - generic "原话与来源" [ref=e173] [cursor=pointer]
                      - button "更正" [ref=e174] [cursor=pointer]
                    - generic [ref=e175]:
                      - generic [ref=e176]: 期望与担心
                      - generic [ref=e177]:
                        - strong [ref=e178]: 整理信息去就医
                        - group [ref=e179]:
                          - generic "原话与来源" [ref=e180] [cursor=pointer]
                      - button "更正" [ref=e181] [cursor=pointer]
                - generic [ref=e182]: 本次已完成结构化信息整理。系统没有据此作出诊断；请结合症状变化和专业医疗评估决定下一步。
                - group [ref=e183]:
                  - generic "哪些变化需要立即求助" [ref=e184]
            - generic [ref=e185]:
              - generic "快捷回答" [ref=e186]:
                - button "摘要准确" [ref=e187] [cursor=pointer]
                - button "我需要更正" [ref=e188] [cursor=pointer]
              - generic [ref=e189]: 请先核对摘要。你可以确认，也可以直接说需要更正的地方。
              - generic [ref=e190]:
                - generic [ref=e191]: 输入你的描述
                - textbox "输入你的描述" [ref=e192]:
                  - /placeholder: 按自己的话说，也可以补充或更正…
                - button "发送回答" [ref=e193] [cursor=pointer]
              - paragraph [ref=e196]:
                - generic [ref=e197]: 0 / 2000
          - generic [ref=e198]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  1   | import { test, expect } from "@playwright/test";
  2   | import {
  3   |   createMeasurementSupplementsModel,
  4   |   verifyMeasurementSupplements,
  5   | } from "../fixtures/measurement-supplements.mjs";
  6   | 
  7   | for (const narrow of [false, true])
  8   |   test(`full measurement reports retain medication updates through failure and retry${narrow ? " on a narrow screen" : ""}`, async ({
  9   |     page,
  10  |     request,
  11  |   }) => {
  12  |     const model = await createMeasurementSupplementsModel();
  13  |     try {
  14  |       expect(
  15  |         (
  16  |           await request.post("/api/settings", {
  17  |             data: {
  18  |               backend: "compatible",
  19  |               base_url: model.endpoint,
  20  |               model: "fixture",
  21  |               api_key: "local-supplements",
  22  |             },
  23  |           })
  24  |         ).ok(),
  25  |       ).toBe(true);
  26  |       if (narrow) await page.setViewportSize({ width: 390, height: 844 });
  27  |       await page.goto("/");
  28  |       await verifyMeasurementSupplements(
  29  |         page,
  30  |         model,
  31  |         `docs/assets/ui/${narrow ? "63-measurement-supplement-narrow" : "62-measurement-supplement"}`,
  32  |       );
  33  | 
> 34  |       test("supplement evidence stays inert and invalid processing status falls back", async ({
      |       ^ Error: Playwright Test did not expect test() to be called here.
  35  |         page,
  36  |       }) => {
  37  |         await page.goto("/");
  38  |         await page.evaluate(async () => {
  39  |           const { createRenderer } = await import("/structured-ui.js");
  40  |           const renderer = createRenderer();
  41  |           const evidence = '<img src=x onerror="window.SUPPLEMENT_EXECUTED=1">';
  42  |           const base = {
  43  |             task_id: "axillary_temperature",
  44  |             key: "temperature_c",
  45  |             title: "腋温记录",
  46  |             unit: "℃",
  47  |             can_repeat: false,
  48  |             total: 1,
  49  |             latest_status: "recorded",
  50  |             entries: [
  51  |               {
  52  |                 id: "one",
  53  |                 status: "recorded",
  54  |                 value: 37.6,
  55  |                 current: true,
  56  |                 source: "patient",
  57  |                 quality: "not_assessed",
  58  |                 recorded_at: "2026-10-17T10:00:00Z",
  59  |                 evidence: "腋温37.6℃",
  60  |               },
  61  |             ],
  62  |             supplement: { text: evidence, status: "forwarded" },
  63  |           };
  64  |           const host = document.createElement("div");
  65  |           host.id = "supplement-contract";
  66  |           for (const [id, data] of [
  67  |             ["safe", base],
  68  |             [
  69  |               "bad-status",
  70  |               { ...base, supplement: { text: evidence, status: "verified" } },
  71  |             ],
  72  |             [
  73  |               "bad-text",
  74  |               {
  75  |                 ...base,
  76  |                 supplement: { text: { html: evidence }, status: "forwarded" },
  77  |               },
  78  |             ],
  79  |           ]) {
  80  |             const section = document.createElement("section");
  81  |             section.id = `supplement-${id}`;
  82  |             section.append(
  83  |               renderer.render({ id, type: "observation_series", data }),
  84  |             );
  85  |             host.append(section);
  86  |           }
  87  |           document.body.append(host);
  88  |         });
  89  |         await expect(
  90  |           page.locator("#supplement-safe .observation-supplement"),
  91  |         ).toContainText("已带入本轮问诊");
  92  |         await expect(page.locator("#supplement-safe img")).toHaveCount(0);
  93  |         expect(
  94  |           await page.evaluate(() => window.SUPPLEMENT_EXECUTED),
  95  |         ).toBeUndefined();
  96  |         for (const name of ["bad-status", "bad-text"])
  97  |           await expect(
  98  |             page.locator(`#supplement-${name} .structured-fallback`),
  99  |           ).toHaveCount(1);
  100 |       });
  101 |     } finally {
  102 |       await request.post("/api/settings", { data: { backend: "demo" } });
  103 |       await model.close();
  104 |     }
  105 |   });
  106 | 
```