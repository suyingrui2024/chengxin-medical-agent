# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: measurement-supplements.spec.mjs >> full measurement reports retain medication updates through failure and retry
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
                  - heading "本次问诊摘要" [active] [level=3] [ref=e76]
                  - paragraph [ref=e77]: 请核对已记录的信息。“未知”表示尚未确认。
                  - generic [ref=e78]:
                    - generic [ref=e79]:
                      - generic [ref=e80]: 腋温记录
                      - button "再测一次" [ref=e81] [cursor=pointer]
                    - generic [ref=e82]: 最新记录
                    - strong [ref=e83]: 37.6 ℃
                    - paragraph [ref=e84]: 患者自述 · 质量未评价
                    - group [ref=e85]:
                      - generic "查看记录 · 2条" [ref=e86] [cursor=pointer]
                    - group [ref=e87]:
                      - generic "补充原话 · 已带入本轮问诊" [ref=e88] [cursor=pointer]
                  - generic [ref=e89]:
                    - generic [ref=e90]:
                      - generic [ref=e91]: 年龄
                      - generic [ref=e95]:
                        - strong [ref=e96]: 32 岁
                        - group [ref=e97]:
                          - generic "原话与来源" [ref=e98] [cursor=pointer]
                      - button "更正" [ref=e99] [cursor=pointer]
                    - generic [ref=e100]:
                      - generic [ref=e101]: 影响程度
                      - generic [ref=e105]:
                        - strong [ref=e106]: 3 / 10
                        - group [ref=e107]:
                          - generic "原话与来源" [ref=e108] [cursor=pointer]
                      - button "更正" [ref=e109] [cursor=pointer]
                  - generic [ref=e110]:
                    - strong [ref=e111]: 仍待确认
                    - generic [ref=e113]:
                      - generic [ref=e114]:
                        - strong [ref=e115]: 过敏信息
                        - button "补充 / 更正" [ref=e116] [cursor=pointer]
                      - blockquote "过敏信息：原话，待确认" [ref=e117]: 过敏不知道
                      - text: 患者自述 · 尚未确认
                      - group [ref=e118]:
                        - generic "查看同轮原话" [ref=e119]
                  - generic [ref=e120]:
                    - heading "当前不适" [level=4] [ref=e121]
                    - generic [ref=e126]:
                      - generic [ref=e127]:
                        - generic [ref=e128]: 主要问题
                        - generic [ref=e129]:
                          - strong [ref=e130]: 咳嗽发烧
                          - group [ref=e131]:
                            - generic "原话与来源" [ref=e132] [cursor=pointer]
                        - button "更正" [ref=e133] [cursor=pointer]
                      - generic [ref=e134]:
                        - generic [ref=e135]: 开始与起病方式
                        - generic [ref=e136]:
                          - strong [ref=e137]: 两天
                          - group [ref=e138]:
                            - generic "原话与来源" [ref=e139] [cursor=pointer]
                        - button "更正" [ref=e140] [cursor=pointer]
                      - generic [ref=e141]:
                        - generic [ref=e142]: 急症筛查
                        - generic [ref=e143]:
                          - strong [ref=e144]: 患者否认上述危险信号
                          - group [ref=e145]:
                            - generic "原话与来源" [ref=e146] [cursor=pointer]
                      - generic [ref=e147]:
                        - generic [ref=e148]: 变化趋势
                        - generic [ref=e149]:
                          - strong [ref=e150]: 变化不大
                          - group [ref=e151]:
                            - generic "原话与来源" [ref=e152] [cursor=pointer]
                        - button "更正" [ref=e153] [cursor=pointer]
                      - generic [ref=e154]:
                        - generic [ref=e155]: 伴随不适
                        - generic [ref=e156]:
                          - strong [ref=e157]: 否认/没有
                          - group [ref=e158]:
                            - generic "原话与来源" [ref=e159] [cursor=pointer]
                        - button "更正" [ref=e160] [cursor=pointer]
                      - generic [ref=e161]:
                        - generic [ref=e162]: 呼吸情况
                        - generic [ref=e163]:
                          - strong [ref=e164]: 没有气促，能完整说话
                          - group [ref=e165]:
                            - generic "原话与来源" [ref=e166] [cursor=pointer]
                        - button "更正" [ref=e167] [cursor=pointer]
                      - generic [ref=e168]:
                        - generic [ref=e169]: 咳嗽细节
                        - generic [ref=e170]:
                          - strong [ref=e171]: 干咳，没有咳血
                          - group [ref=e172]:
                            - generic "原话与来源" [ref=e173] [cursor=pointer]
                        - button "更正" [ref=e174] [cursor=pointer]
                      - generic [ref=e175]:
                        - generic [ref=e176]: 饮水进食与排尿
                        - generic [ref=e177]:
                          - strong [ref=e178]: 能正常喝水吃饭，排尿正常
                          - group [ref=e179]:
                            - generic "原话与来源" [ref=e180] [cursor=pointer]
                        - button "更正" [ref=e181] [cursor=pointer]
                  - generic [ref=e182]:
                    - heading "用药与背景" [level=4] [ref=e183]
                    - generic [ref=e188]:
                      - generic [ref=e189]:
                        - generic [ref=e190]: 咨询对象
                        - generic [ref=e191]:
                          - strong [ref=e192]: 本人
                          - group [ref=e193]:
                            - generic "原话与来源" [ref=e194] [cursor=pointer]
                        - button "更正" [ref=e195] [cursor=pointer]
                      - generic [ref=e196]:
                        - generic [ref=e197]: 已做处理/用药
                        - generic [ref=e198]:
                          - strong [ref=e199]: 昨晚用药物甲2次，具体用量不清楚
                          - group [ref=e200]:
                            - generic "原话与来源" [ref=e201] [cursor=pointer]
                        - button "更正" [ref=e202] [cursor=pointer]
                      - generic [ref=e203]:
                        - generic [ref=e204]: 相关病史
                        - generic [ref=e205]:
                          - strong [ref=e206]: 否认/没有
                          - group [ref=e207]:
                            - generic "原话与来源" [ref=e208] [cursor=pointer]
                        - button "更正" [ref=e209] [cursor=pointer]
                      - generic [ref=e210]:
                        - generic [ref=e211]: 期望与担心
                        - generic [ref=e212]:
                          - strong [ref=e213]: 整理信息去就医
                          - group [ref=e214]:
                            - generic "原话与来源" [ref=e215] [cursor=pointer]
                        - button "更正" [ref=e216] [cursor=pointer]
                  - generic [ref=e217]: 本次已完成结构化信息整理。系统没有据此作出诊断；请结合症状变化和专业医疗评估决定下一步。
                  - group [ref=e218]:
                    - generic "哪些变化需要立即求助" [ref=e219]
              - generic [ref=e220]:
                - generic "快捷回答" [ref=e221]:
                  - button "摘要准确" [ref=e222] [cursor=pointer]
                  - button "我需要更正" [ref=e223] [cursor=pointer]
                - generic [ref=e224]: 请先核对摘要。你可以确认，也可以直接说需要更正的地方。
                - generic [ref=e225]:
                  - generic [ref=e226]: 输入你的描述
                  - textbox "输入你的描述" [ref=e227]:
                    - /placeholder: 按自己的话说，也可以补充或更正…
                  - button "发送回答" [ref=e228] [cursor=pointer]
                - paragraph [ref=e231]:
                  - generic [ref=e232]: 0 / 2000
            - complementary "问诊记录" [ref=e233]:
              - generic [ref=e234]:
                - generic [ref=e235]: 本次问诊
                - generic [ref=e236]: 待核对
              - strong [ref=e238]: 请核对整理的内容
              - generic [ref=e239]:
                - heading "已记录的信息" [level=2] [ref=e240]
                - generic [ref=e241]: 16 项
              - generic [ref=e242]:
                - generic [ref=e243]:
                  - generic [ref=e244]:
                    - generic [ref=e245]: 咨询对象
                    - button "更正" [ref=e246] [cursor=pointer]
                  - paragraph [ref=e247]: 本人
                - generic [ref=e248]:
                  - generic [ref=e249]:
                    - generic [ref=e250]: 年龄
                    - button "更正" [ref=e251] [cursor=pointer]
                  - paragraph [ref=e252]: 32 岁
                - generic [ref=e253]:
                  - generic [ref=e254]:
                    - generic [ref=e255]: 主要问题
                    - button "更正" [ref=e256] [cursor=pointer]
                  - paragraph [ref=e257]: 咳嗽发烧
                - generic [ref=e258]:
                  - generic [ref=e259]:
                    - generic [ref=e260]: 开始时间
                    - button "更正" [ref=e261] [cursor=pointer]
                  - paragraph [ref=e262]: 两天
                - generic [ref=e263]:
                  - generic [ref=e264]: 紧急信号筛查
                  - paragraph [ref=e266]: 用户否认上述紧急信号
                - generic [ref=e267]:
                  - generic [ref=e268]: 报告的腋温
                  - paragraph [ref=e270]: 37.6 ℃
                - generic [ref=e271]:
                  - generic [ref=e272]:
                    - generic [ref=e273]: 变化趋势
                    - button "更正" [ref=e274] [cursor=pointer]
                  - paragraph [ref=e275]: 变化不大
                - generic [ref=e276]:
                  - generic [ref=e277]:
                    - generic [ref=e278]: 影响程度
                    - button "更正" [ref=e279] [cursor=pointer]
                  - paragraph [ref=e280]: "3"
                - generic [ref=e281]:
                  - generic [ref=e282]:
                    - generic [ref=e283]: 伴随不适
                    - button "更正" [ref=e284] [cursor=pointer]
                  - paragraph [ref=e285]: 明确否认
                - generic [ref=e286]:
                  - generic [ref=e287]:
                    - generic [ref=e288]: 呼吸情况
                    - button "更正" [ref=e289] [cursor=pointer]
                  - paragraph [ref=e290]: 没有气促，能完整说话
                - generic [ref=e291]:
                  - generic [ref=e292]:
                    - generic [ref=e293]: 咳嗽情况
                    - button "更正" [ref=e294] [cursor=pointer]
                  - paragraph [ref=e295]: 干咳，没有咳血
                - generic [ref=e296]:
                  - generic [ref=e297]:
                    - generic [ref=e298]: 饮水、进食与排尿
                    - button "更正" [ref=e299] [cursor=pointer]
                  - paragraph [ref=e300]: 能正常喝水吃饭，排尿正常
                - generic [ref=e301]:
                  - generic [ref=e302]:
                    - generic [ref=e303]: 用药与处理
                    - button "更正" [ref=e304] [cursor=pointer]
                  - paragraph [ref=e305]: 昨晚用药物甲2次，具体用量不清楚
                - generic [ref=e306]:
                  - generic [ref=e307]:
                    - generic [ref=e308]: 过敏信息
                    - button "更正" [ref=e309] [cursor=pointer]
                  - paragraph [ref=e310]: 未知 / 尚未确认
                - generic [ref=e311]:
                  - generic [ref=e312]:
                    - generic [ref=e313]: 相关病史
                    - button "更正" [ref=e314] [cursor=pointer]
                  - paragraph [ref=e315]: 明确否认
                - generic [ref=e316]:
                  - generic [ref=e317]:
                    - generic [ref=e318]: 期望与担心
                    - button "更正" [ref=e319] [cursor=pointer]
                  - paragraph [ref=e320]: 整理信息去就医
              - generic [ref=e321]:
                - generic [ref=e322]: 已提供
                - generic [ref=e324]: 待确认 / 未知
              - paragraph [ref=e326]: 记录可以更正。没有提到的信息，不会自动记为“没有”。
          - generic [ref=e327]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
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