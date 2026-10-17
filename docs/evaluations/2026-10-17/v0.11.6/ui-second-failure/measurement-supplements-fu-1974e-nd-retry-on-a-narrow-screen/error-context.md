# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: measurement-supplements.spec.mjs >> full measurement reports retain medication updates through failure and retry on a narrow screen
- Location: tests/ui/measurement-supplements.spec.mjs:8:3

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: true
Received: false
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
                - heading "本次问诊摘要" [active] [level=3] [ref=e40]
                - paragraph [ref=e41]: 请核对已记录的信息。“未知”表示尚未确认。
                - generic [ref=e42]:
                  - generic [ref=e43]:
                    - generic [ref=e44]: 腋温记录
                    - button "再测一次" [ref=e45] [cursor=pointer]
                  - generic [ref=e46]: 最新记录
                  - strong [ref=e47]: 37.6 ℃
                  - paragraph [ref=e48]: 患者自述 · 质量未评价
                  - group [ref=e49]:
                    - generic "查看记录 · 2条" [ref=e50] [cursor=pointer]
                  - group [ref=e51]:
                    - generic "补充原话 · 已带入本轮问诊" [ref=e52] [cursor=pointer]
                - generic [ref=e53]:
                  - generic [ref=e54]:
                    - generic [ref=e55]: 年龄
                    - generic [ref=e59]:
                      - strong [ref=e60]: 32 岁
                      - group [ref=e61]:
                        - generic "原话与来源" [ref=e62] [cursor=pointer]
                    - button "更正" [ref=e63] [cursor=pointer]
                  - generic [ref=e64]:
                    - generic [ref=e65]: 影响程度
                    - generic [ref=e69]:
                      - strong [ref=e70]: 3 / 10
                      - group [ref=e71]:
                        - generic "原话与来源" [ref=e72] [cursor=pointer]
                    - button "更正" [ref=e73] [cursor=pointer]
                - generic [ref=e74]:
                  - strong [ref=e75]: 仍待确认
                  - generic [ref=e77]:
                    - generic [ref=e78]:
                      - strong [ref=e79]: 过敏信息
                      - button "补充 / 更正" [ref=e80] [cursor=pointer]
                    - blockquote "过敏信息：原话，待确认" [ref=e81]: 过敏不知道
                    - text: 患者自述 · 尚未确认
                    - group [ref=e82]:
                      - generic "查看同轮原话" [ref=e83]
                - generic [ref=e84]:
                  - heading "当前不适" [level=4] [ref=e85]
                  - generic [ref=e90]:
                    - generic [ref=e91]:
                      - generic [ref=e92]: 主要问题
                      - generic [ref=e93]:
                        - strong [ref=e94]: 咳嗽发烧
                        - group [ref=e95]:
                          - generic "原话与来源" [ref=e96] [cursor=pointer]
                      - button "更正" [ref=e97] [cursor=pointer]
                    - generic [ref=e98]:
                      - generic [ref=e99]: 开始与起病方式
                      - generic [ref=e100]:
                        - strong [ref=e101]: 两天
                        - group [ref=e102]:
                          - generic "原话与来源" [ref=e103] [cursor=pointer]
                      - button "更正" [ref=e104] [cursor=pointer]
                    - generic [ref=e105]:
                      - generic [ref=e106]: 急症筛查
                      - generic [ref=e107]:
                        - strong [ref=e108]: 患者否认上述危险信号
                        - group [ref=e109]:
                          - generic "原话与来源" [ref=e110] [cursor=pointer]
                    - generic [ref=e111]:
                      - generic [ref=e112]: 变化趋势
                      - generic [ref=e113]:
                        - strong [ref=e114]: 变化不大
                        - group [ref=e115]:
                          - generic "原话与来源" [ref=e116] [cursor=pointer]
                      - button "更正" [ref=e117] [cursor=pointer]
                    - generic [ref=e118]:
                      - generic [ref=e119]: 伴随不适
                      - generic [ref=e120]:
                        - strong [ref=e121]: 否认/没有
                        - group [ref=e122]:
                          - generic "原话与来源" [ref=e123] [cursor=pointer]
                      - button "更正" [ref=e124] [cursor=pointer]
                    - generic [ref=e125]:
                      - generic [ref=e126]: 呼吸情况
                      - generic [ref=e127]:
                        - strong [ref=e128]: 没有气促，能完整说话
                        - group [ref=e129]:
                          - generic "原话与来源" [ref=e130] [cursor=pointer]
                      - button "更正" [ref=e131] [cursor=pointer]
                    - generic [ref=e132]:
                      - generic [ref=e133]: 咳嗽细节
                      - generic [ref=e134]:
                        - strong [ref=e135]: 干咳，没有咳血
                        - group [ref=e136]:
                          - generic "原话与来源" [ref=e137] [cursor=pointer]
                      - button "更正" [ref=e138] [cursor=pointer]
                    - generic [ref=e139]:
                      - generic [ref=e140]: 饮水进食与排尿
                      - generic [ref=e141]:
                        - strong [ref=e142]: 能正常喝水吃饭，排尿正常
                        - group [ref=e143]:
                          - generic "原话与来源" [ref=e144] [cursor=pointer]
                      - button "更正" [ref=e145] [cursor=pointer]
                - generic [ref=e146]:
                  - heading "用药与背景" [level=4] [ref=e147]
                  - generic [ref=e152]:
                    - generic [ref=e153]:
                      - generic [ref=e154]: 咨询对象
                      - generic [ref=e155]:
                        - strong [ref=e156]: 本人
                        - group [ref=e157]:
                          - generic "原话与来源" [ref=e158] [cursor=pointer]
                      - button "更正" [ref=e159] [cursor=pointer]
                    - generic [ref=e160]:
                      - generic [ref=e161]: 已做处理/用药
                      - generic [ref=e162]:
                        - strong [ref=e163]: 昨晚用药物甲2次，具体用量不清楚
                        - group [ref=e164]:
                          - generic "原话与来源" [ref=e165] [cursor=pointer]
                      - button "更正" [ref=e166] [cursor=pointer]
                    - generic [ref=e167]:
                      - generic [ref=e168]: 相关病史
                      - generic [ref=e169]:
                        - strong [ref=e170]: 否认/没有
                        - group [ref=e171]:
                          - generic "原话与来源" [ref=e172] [cursor=pointer]
                      - button "更正" [ref=e173] [cursor=pointer]
                    - generic [ref=e174]:
                      - generic [ref=e175]: 期望与担心
                      - generic [ref=e176]:
                        - strong [ref=e177]: 整理信息去就医
                        - group [ref=e178]:
                          - generic "原话与来源" [ref=e179] [cursor=pointer]
                      - button "更正" [ref=e180] [cursor=pointer]
                - generic [ref=e181]: 本次已完成结构化信息整理。系统没有据此作出诊断；请结合症状变化和专业医疗评估决定下一步。
                - group [ref=e182]:
                  - generic "哪些变化需要立即求助" [ref=e183]
            - generic [ref=e184]:
              - generic "快捷回答" [ref=e185]:
                - button "摘要准确" [ref=e186] [cursor=pointer]
                - button "我需要更正" [ref=e187] [cursor=pointer]
              - generic [ref=e188]: 请先核对摘要。你可以确认，也可以直接说需要更正的地方。
              - generic [ref=e189]:
                - generic [ref=e190]: 输入你的描述
                - textbox "输入你的描述" [ref=e191]:
                  - /placeholder: 按自己的话说，也可以补充或更正…
                - button "发送回答" [ref=e192] [cursor=pointer]
              - paragraph [ref=e195]:
                - generic [ref=e196]: 0 / 2000
          - generic [ref=e197]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  6   | const report = `腋温37.6℃；更正：${medication}。`;
  7   | const secondReport = `腋温38.6℃，读数可能不准；更正：${secondMedication}。`;
  8   | const update = (value) => ({
  9   |   tool: "record_facts",
  10  |   arguments: {
  11  |     facts: [
  12  |       {
  13  |         key: "medications_taken",
  14  |         value,
  15  |         evidence: value,
  16  |         assertion: "present",
  17  |         correction: true,
  18  |       },
  19  |     ],
  20  |   },
  21  | });
  22  | 
  23  | export function createMeasurementSupplementsModel() {
  24  |   return createRepeatedObservationModel([
  25  |     null,
  26  |     update(medication),
  27  |     { tool: "review_summary", arguments: {} },
  28  |     update(secondMedication),
  29  |     { tool: "review_summary", arguments: {} },
  30  |   ]);
  31  | }
  32  | 
  33  | export async function verifyMeasurementSupplements(
  34  |   page,
  35  |   model,
  36  |   screenshotPrefix,
  37  | ) {
  38  |   await expect(page.getByText("本机服务已连接")).toBeVisible();
  39  |   const created = page.waitForResponse(
  40  |     (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  41  |   );
  42  |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  43  |   const id = (await (await created).json()).session_id;
  44  |   const send = async (text) => {
  45  |     await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  46  |     await page.getByRole("button", { name: "发送回答" }).click();
  47  |   };
  48  |   const current = () => page.locator(".summary-card.current-summary");
  49  |   await send(
  50  |     "我自己32岁，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。",
  51  |   );
  52  |   await expect(page.locator("#active-task")).toBeVisible();
  53  |   await send("腋温38.25℃");
  54  |   await expect(page.locator("#active-question")).toBeVisible();
  55  |   await send(
  56  |     "总体变化不大；不适程度3分；没有其他不适；没有气促，能完整说话；干咳，没有咳血；能正常喝水吃饭，排尿正常；没有用药；过敏不知道；没有相关既往病史；想整理信息去就医。",
  57  |   );
  58  |   await expect(current().locator(".observation-value")).toHaveText("38.25 ℃");
  59  |   expect(model.requests).toHaveLength(4);
  60  |   await current()
  61  |     .getByRole("button", { name: "再测一次", exact: true })
  62  |     .click();
  63  |   await send(report);
  64  |   await expect(
  65  |     page.getByRole("button", { name: "重试这一轮", exact: true }),
  66  |   ).toBeVisible();
  67  |   const pending = page.locator(".observation-supplement").last();
  68  |   await expect(pending).toContainText("补充信息 · 待重试");
  69  |   await pending.locator(":scope > summary").click();
  70  |   await expect(pending.locator("blockquote")).toHaveText(report);
  71  |   const snapshot = () =>
  72  |     page.evaluate(
  73  |       async (sessionId) =>
  74  |         (await fetch(`/api/sessions/${sessionId}/snapshot`)).json(),
  75  |       id,
  76  |     );
  77  |   const failed = await snapshot();
  78  |   expect(failed.state.facts.temperature_c.value).toBe(37.6);
  79  |   const temperatureId = failed.state.facts.temperature_c.fact_id;
  80  |   if (screenshotPrefix)
  81  |     await page.screenshot({
  82  |       path: `${screenshotPrefix}-pending.png`,
  83  |       fullPage: true,
  84  |     });
  85  |   await page.getByRole("button", { name: "重试这一轮", exact: true }).click();
  86  |   await expect(
  87  |     current().locator(".summary-item[data-fact-key='medications_taken']"),
  88  |   ).toContainText(medication);
  89  |   await expect(current().locator(".observation-supplement")).toContainText(
  90  |     "已带入本轮问诊",
  91  |   );
  92  |   const repaired = await snapshot();
  93  |   expect(repaired.state.facts.temperature_c.fact_id).toBe(temperatureId);
  94  |   expect(
  95  |     repaired.state.history
  96  |       .filter((f) => f.key === "temperature_c")
  97  |       .map((f) => f.value),
  98  |   ).toEqual([38.25, 37.6]);
  99  |   expect(repaired.state.facts.temperature_c.turn_id).toBe(
  100 |     repaired.state.facts.medications_taken.turn_id,
  101 |   );
  102 |   expect(model.requests).toHaveLength(7);
  103 |   const observed = model.requests
  104 |     .slice(4)
  105 |     .map((r) => JSON.parse(r.messages.at(-1).content));
> 106 |   expect(observed.every((c) => c.latest_user_text === report)).toBe(true);
      |                                                                ^ Error: expect(received).toBe(expected) // Object.is equality
  107 |   await current()
  108 |     .getByRole("button", { name: "再测一次", exact: true })
  109 |     .click();
  110 |   await send(secondReport);
  111 |   await expect(
  112 |     current().locator(".summary-item[data-fact-key='medications_taken']"),
  113 |   ).toContainText(secondMedication);
  114 |   await expect(current().locator(".observation-value")).toHaveText("37.6 ℃");
  115 |   await expect(current().locator(".observation-current-label")).toHaveText(
  116 |     "上次记录 · 本次未完成",
  117 |   );
  118 |   const supplement = current().locator(".observation-supplement");
  119 |   await supplement.locator(":scope > summary").click();
  120 |   await expect(supplement.locator("blockquote")).toHaveText(secondReport);
  121 |   const final = await snapshot();
  122 |   expect(final.state.observation_attempts.map((a) => a.status)).toEqual([
  123 |     "recorded",
  124 |     "unconfirmed",
  125 |   ]);
  126 |   expect(
  127 |     final.state.history.filter((f) => f.key === "temperature_c"),
  128 |   ).toHaveLength(2);
  129 |   expect(model.requests).toHaveLength(9);
  130 |   expect(
  131 |     await page.evaluate(
  132 |       () => document.documentElement.scrollWidth <= innerWidth,
  133 |     ),
  134 |   ).toBe(true);
  135 |   if (screenshotPrefix)
  136 |     await page.screenshot({
  137 |       path: `${screenshotPrefix}-summary.png`,
  138 |       fullPage: true,
  139 |     });
  140 | }
  141 | 
```