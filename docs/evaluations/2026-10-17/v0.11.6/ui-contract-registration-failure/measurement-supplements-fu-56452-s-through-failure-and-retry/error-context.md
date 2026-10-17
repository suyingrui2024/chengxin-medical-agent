# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: measurement-supplements.spec.mjs >> full measurement reports retain medication updates through failure and retry
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
        - button "会谈记录 1" [ref=e21] [cursor=pointer]:
          - text: 会谈记录
          - generic [ref=e24]: "1"
      - generic [ref=e25]:
        - generic [ref=e26]: 最近问诊
        - generic [ref=e27]: "1"
      - button "·咳嗽发烧" [ref=e29] [cursor=pointer]
      - generic [ref=e30]:
        - generic [ref=e33]:
          - text: 本机工作空间
          - generic [ref=e34]: 会谈仅保留到服务关闭
        - generic [ref=e35]:
          - generic [ref=e36]: 研究原型
          - generic [ref=e37]: 能力协议 v1
    - generic [ref=e38]:
      - banner [ref=e39]:
        - generic [ref=e40]:
          - generic [ref=e41]: 工作空间
          - generic [ref=e42]: /
          - strong [ref=e43]: 问诊
        - generic [ref=e44]:
          - generic [ref=e45]: 本机服务已连接
          - generic [ref=e47]: 直接模型
      - main [ref=e48]:
        - region "问诊" [ref=e49]:
          - generic [ref=e50]:
            - heading "核对本次记录" [level=1] [ref=e52]
            - button "问诊记录 16" [ref=e53] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e56]: "16"
          - generic [ref=e57]:
            - region "问诊对话" [ref=e58]:
              - generic [ref=e59]:
                - generic [ref=e62]:
                  - strong [ref=e63]: 澄心问诊助手
                  - generic [ref=e64]: 直接模型 · 本次会谈
                - generic [ref=e65]:
                  - generic [ref=e66]: 待核对
                  - button "紧急求助" [ref=e67] [cursor=pointer]
              - generic [ref=e70]:
                - status [ref=e71]: 核对记录 · 可以更正
                - button "展开对话记录" [ref=e72] [cursor=pointer]
              - log "会谈消息" [ref=e74]:
                - article [ref=e75]:
                  - heading "本次问诊摘要" [level=3] [ref=e76]
                  - paragraph [ref=e77]: 请核对已记录的信息。“未知”表示尚未确认。
                  - generic [ref=e78]:
                    - generic [ref=e79]:
                      - generic [ref=e80]: 腋温记录
                      - button "再测一次" [ref=e81] [cursor=pointer]
                    - generic [ref=e82]: 上次记录 · 本次未完成
                    - strong [ref=e83]: 37.6 ℃
                    - paragraph [ref=e84]: 患者自述 · 质量未评价
                    - group [ref=e85]:
                      - generic "查看记录 · 3条" [ref=e86] [cursor=pointer]
                    - group [ref=e87]:
                      - generic "补充原话 · 已带入本轮问诊" [active] [ref=e88] [cursor=pointer]
                      - blockquote [ref=e89]: 腋温38.6℃，读数可能不准；更正：今天用过药物乙，用量不清楚。
                  - generic [ref=e90]:
                    - generic [ref=e91]:
                      - generic [ref=e92]: 年龄
                      - generic [ref=e96]:
                        - strong [ref=e97]: 32 岁
                        - group [ref=e98]:
                          - generic "原话与来源" [ref=e99] [cursor=pointer]
                      - button "更正" [ref=e100] [cursor=pointer]
                    - generic [ref=e101]:
                      - generic [ref=e102]: 影响程度
                      - generic [ref=e106]:
                        - strong [ref=e107]: 3 / 10
                        - group [ref=e108]:
                          - generic "原话与来源" [ref=e109] [cursor=pointer]
                      - button "更正" [ref=e110] [cursor=pointer]
                  - generic [ref=e111]:
                    - strong [ref=e112]: 仍待确认
                    - generic [ref=e114]:
                      - generic [ref=e115]:
                        - strong [ref=e116]: 过敏信息
                        - button "补充 / 更正" [ref=e117] [cursor=pointer]
                      - blockquote "过敏信息：原话，待确认" [ref=e118]: 过敏不知道
                      - text: 患者自述 · 尚未确认
                      - group [ref=e119]:
                        - generic "查看同轮原话" [ref=e120]
                  - generic [ref=e121]:
                    - heading "当前不适" [level=4] [ref=e122]
                    - generic [ref=e127]:
                      - generic [ref=e128]:
                        - generic [ref=e129]: 主要问题
                        - generic [ref=e130]:
                          - strong [ref=e131]: 咳嗽发烧
                          - group [ref=e132]:
                            - generic "原话与来源" [ref=e133] [cursor=pointer]
                        - button "更正" [ref=e134] [cursor=pointer]
                      - generic [ref=e135]:
                        - generic [ref=e136]: 开始与起病方式
                        - generic [ref=e137]:
                          - strong [ref=e138]: 两天
                          - group [ref=e139]:
                            - generic "原话与来源" [ref=e140] [cursor=pointer]
                        - button "更正" [ref=e141] [cursor=pointer]
                      - generic [ref=e142]:
                        - generic [ref=e143]: 急症筛查
                        - generic [ref=e144]:
                          - strong [ref=e145]: 患者否认上述危险信号
                          - group [ref=e146]:
                            - generic "原话与来源" [ref=e147] [cursor=pointer]
                      - generic [ref=e148]:
                        - generic [ref=e149]: 变化趋势
                        - generic [ref=e150]:
                          - strong [ref=e151]: 变化不大
                          - group [ref=e152]:
                            - generic "原话与来源" [ref=e153] [cursor=pointer]
                        - button "更正" [ref=e154] [cursor=pointer]
                      - generic [ref=e155]:
                        - generic [ref=e156]: 伴随不适
                        - generic [ref=e157]:
                          - strong [ref=e158]: 否认/没有
                          - group [ref=e159]:
                            - generic "原话与来源" [ref=e160] [cursor=pointer]
                        - button "更正" [ref=e161] [cursor=pointer]
                      - generic [ref=e162]:
                        - generic [ref=e163]: 呼吸情况
                        - generic [ref=e164]:
                          - strong [ref=e165]: 没有气促，能完整说话
                          - group [ref=e166]:
                            - generic "原话与来源" [ref=e167] [cursor=pointer]
                        - button "更正" [ref=e168] [cursor=pointer]
                      - generic [ref=e169]:
                        - generic [ref=e170]: 咳嗽细节
                        - generic [ref=e171]:
                          - strong [ref=e172]: 干咳，没有咳血
                          - group [ref=e173]:
                            - generic "原话与来源" [ref=e174] [cursor=pointer]
                        - button "更正" [ref=e175] [cursor=pointer]
                      - generic [ref=e176]:
                        - generic [ref=e177]: 饮水进食与排尿
                        - generic [ref=e178]:
                          - strong [ref=e179]: 能正常喝水吃饭，排尿正常
                          - group [ref=e180]:
                            - generic "原话与来源" [ref=e181] [cursor=pointer]
                        - button "更正" [ref=e182] [cursor=pointer]
                  - generic [ref=e183]:
                    - heading "用药与背景" [level=4] [ref=e184]
                    - generic [ref=e189]:
                      - generic [ref=e190]:
                        - generic [ref=e191]: 咨询对象
                        - generic [ref=e192]:
                          - strong [ref=e193]: 本人
                          - group [ref=e194]:
                            - generic "原话与来源" [ref=e195] [cursor=pointer]
                        - button "更正" [ref=e196] [cursor=pointer]
                      - generic [ref=e197]:
                        - generic [ref=e198]: 已做处理/用药
                        - generic [ref=e199]:
                          - strong [ref=e200]: 今天用过药物乙，用量不清楚
                          - group [ref=e201]:
                            - generic "原话与来源" [ref=e202] [cursor=pointer]
                        - button "更正" [ref=e203] [cursor=pointer]
                      - generic [ref=e204]:
                        - generic [ref=e205]: 相关病史
                        - generic [ref=e206]:
                          - strong [ref=e207]: 否认/没有
                          - group [ref=e208]:
                            - generic "原话与来源" [ref=e209] [cursor=pointer]
                        - button "更正" [ref=e210] [cursor=pointer]
                      - generic [ref=e211]:
                        - generic [ref=e212]: 期望与担心
                        - generic [ref=e213]:
                          - strong [ref=e214]: 整理信息去就医
                          - group [ref=e215]:
                            - generic "原话与来源" [ref=e216] [cursor=pointer]
                        - button "更正" [ref=e217] [cursor=pointer]
                  - generic [ref=e218]: 本次已完成结构化信息整理。系统没有据此作出诊断；请结合症状变化和专业医疗评估决定下一步。
                  - group [ref=e219]:
                    - generic "哪些变化需要立即求助" [ref=e220]
              - generic [ref=e221]:
                - generic "快捷回答" [ref=e222]:
                  - button "摘要准确" [ref=e223] [cursor=pointer]
                  - button "我需要更正" [ref=e224] [cursor=pointer]
                - generic [ref=e225]: 请先核对摘要。你可以确认，也可以直接说需要更正的地方。
                - generic [ref=e226]:
                  - generic [ref=e227]: 输入你的描述
                  - textbox "输入你的描述" [ref=e228]:
                    - /placeholder: 按自己的话说，也可以补充或更正…
                  - button "发送回答" [ref=e229] [cursor=pointer]
                - paragraph [ref=e232]:
                  - generic [ref=e233]: 0 / 2000
            - complementary "问诊记录" [ref=e234]:
              - generic [ref=e235]:
                - generic [ref=e236]: 本次问诊
                - generic [ref=e237]: 待核对
              - strong [ref=e239]: 请核对整理的内容
              - generic [ref=e240]:
                - heading "已记录的信息" [level=2] [ref=e241]
                - generic [ref=e242]: 16 项
              - generic [ref=e243]:
                - generic [ref=e244]:
                  - generic [ref=e245]:
                    - generic [ref=e246]: 咨询对象
                    - button "更正" [ref=e247] [cursor=pointer]
                  - paragraph [ref=e248]: 本人
                - generic [ref=e249]:
                  - generic [ref=e250]:
                    - generic [ref=e251]: 年龄
                    - button "更正" [ref=e252] [cursor=pointer]
                  - paragraph [ref=e253]: 32 岁
                - generic [ref=e254]:
                  - generic [ref=e255]:
                    - generic [ref=e256]: 主要问题
                    - button "更正" [ref=e257] [cursor=pointer]
                  - paragraph [ref=e258]: 咳嗽发烧
                - generic [ref=e259]:
                  - generic [ref=e260]:
                    - generic [ref=e261]: 开始时间
                    - button "更正" [ref=e262] [cursor=pointer]
                  - paragraph [ref=e263]: 两天
                - generic [ref=e264]:
                  - generic [ref=e265]: 紧急信号筛查
                  - paragraph [ref=e267]: 用户否认上述紧急信号
                - generic [ref=e268]:
                  - generic [ref=e269]: 报告的腋温
                  - paragraph [ref=e271]: 37.6 ℃ · 上次记录，本次未完成
                - generic [ref=e272]:
                  - generic [ref=e273]:
                    - generic [ref=e274]: 变化趋势
                    - button "更正" [ref=e275] [cursor=pointer]
                  - paragraph [ref=e276]: 变化不大
                - generic [ref=e277]:
                  - generic [ref=e278]:
                    - generic [ref=e279]: 影响程度
                    - button "更正" [ref=e280] [cursor=pointer]
                  - paragraph [ref=e281]: "3"
                - generic [ref=e282]:
                  - generic [ref=e283]:
                    - generic [ref=e284]: 伴随不适
                    - button "更正" [ref=e285] [cursor=pointer]
                  - paragraph [ref=e286]: 明确否认
                - generic [ref=e287]:
                  - generic [ref=e288]:
                    - generic [ref=e289]: 呼吸情况
                    - button "更正" [ref=e290] [cursor=pointer]
                  - paragraph [ref=e291]: 没有气促，能完整说话
                - generic [ref=e292]:
                  - generic [ref=e293]:
                    - generic [ref=e294]: 咳嗽情况
                    - button "更正" [ref=e295] [cursor=pointer]
                  - paragraph [ref=e296]: 干咳，没有咳血
                - generic [ref=e297]:
                  - generic [ref=e298]:
                    - generic [ref=e299]: 饮水、进食与排尿
                    - button "更正" [ref=e300] [cursor=pointer]
                  - paragraph [ref=e301]: 能正常喝水吃饭，排尿正常
                - generic [ref=e302]:
                  - generic [ref=e303]:
                    - generic [ref=e304]: 用药与处理
                    - button "更正" [ref=e305] [cursor=pointer]
                  - paragraph [ref=e306]: 今天用过药物乙，用量不清楚
                - generic [ref=e307]:
                  - generic [ref=e308]:
                    - generic [ref=e309]: 过敏信息
                    - button "更正" [ref=e310] [cursor=pointer]
                  - paragraph [ref=e311]: 未知 / 尚未确认
                - generic [ref=e312]:
                  - generic [ref=e313]:
                    - generic [ref=e314]: 相关病史
                    - button "更正" [ref=e315] [cursor=pointer]
                  - paragraph [ref=e316]: 明确否认
                - generic [ref=e317]:
                  - generic [ref=e318]:
                    - generic [ref=e319]: 期望与担心
                    - button "更正" [ref=e320] [cursor=pointer]
                  - paragraph [ref=e321]: 整理信息去就医
              - generic [ref=e322]:
                - generic [ref=e323]: 已提供
                - generic [ref=e325]: 待确认 / 未知
              - paragraph [ref=e327]: 记录可以更正。没有提到的信息，不会自动记为“没有”。
          - generic [ref=e328]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
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