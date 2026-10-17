# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: uncertain-reports.spec.mjs >> unresolved narratives remain visible and editable through a complete consultation
- Location: tests/ui/uncertain-reports.spec.mjs:4:1

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: locator.click: Test timeout of 30000ms exceeded.
Call log:
  - waiting for getByRole('button', { name: '暂时跳过', exact: true })

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
      - button "·手臂疼痛" [ref=e29] [cursor=pointer]
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
          - generic [ref=e47]: 离线演示
      - main [ref=e48]:
        - region "问诊" [ref=e49]:
          - generic [ref=e50]:
            - heading "观察与记录" [level=1] [ref=e52]
            - button "问诊记录 8" [ref=e53] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e56]: "8"
          - generic [ref=e57]:
            - region "问诊对话" [ref=e58]:
              - generic [ref=e59]:
                - generic [ref=e62]:
                  - strong [ref=e63]: 澄心问诊助手
                  - generic [ref=e64]: 离线演示 · 本次会谈
                - generic [ref=e65]:
                  - generic [ref=e66]: 定向观察
                  - button "紧急求助" [ref=e67] [cursor=pointer]
              - generic [ref=e70]:
                - status [ref=e71]: 按图观察 · 可以跳过
                - button "展开对话记录" [ref=e72] [cursor=pointer]
              - generic "当前图示任务" [ref=e73]:
                - article [ref=e74]:
                  - generic [ref=e75]:
                    - heading "指出最明显的不适部位" [active] [level=3] [ref=e77]
                    - generic [ref=e78]: 待临床审核
                  - generic [ref=e79]:
                    - figure [ref=e80]:
                      - group "选择主观不适部位，不需要按压身体" [ref=e81]:
                        - generic [ref=e82]:
                          - img [aria-hidden] [ref=e83]:
                            - generic [ref=e87]:
                              - generic [ref=e88]: 正面
                              - generic [ref=e89]: 背面
                          - button "头/面部" [ref=e90] [cursor=pointer]
                          - button "胸部" [ref=e91] [cursor=pointer]
                          - button "腹部" [ref=e92] [cursor=pointer]
                          - button "背/腰部" [ref=e93] [cursor=pointer]
                          - button "四肢" [ref=e94] [cursor=pointer]
                        - paragraph [ref=e95]: 点击部位，再确认记录
                        - button "先选择部位" [disabled] [ref=e96]
                    - generic [ref=e97]:
                      - generic [ref=e98]: 1 / 2
                      - paragraph [ref=e102]: 看图找到最明显的位置。
                      - generic [ref=e103]:
                        - button "上一步" [disabled] [ref=e104]
                        - button "下一步" [ref=e105] [cursor=pointer]
                  - paragraph [ref=e106]: 停止 / 跳过：无法确定位置时可跳过
                  - group [ref=e107]:
                    - generic "为什么观察 · 使用限制" [ref=e108] [cursor=pointer]
                  - paragraph [ref=e109]: 选一个部位，或用自己的话描述位置。
              - generic [ref=e110]:
                - generic "快捷回答" [ref=e111]:
                  - button "其他部位" [ref=e112] [cursor=pointer]
                  - button "说不清/跳过" [ref=e113] [cursor=pointer]
                - generic [ref=e114]: 按图示完成后记录结果；做不到也可以跳过。
                - generic [ref=e115]:
                  - generic [ref=e116]: 输入你的描述
                  - textbox "输入你的描述" [ref=e117]:
                    - /placeholder: 按自己的话说，也可以补充或更正…
                  - button "发送回答" [ref=e118] [cursor=pointer]
                - paragraph [ref=e121]:
                  - generic [ref=e122]: 0 / 2000
            - complementary "问诊记录" [ref=e123]:
              - generic [ref=e124]:
                - generic [ref=e125]: 本次问诊
                - generic [ref=e126]: 定向观察
              - strong [ref=e128]: 记录一次定向观察
              - generic [ref=e129]:
                - heading "已记录的信息" [level=2] [ref=e130]
                - generic [ref=e131]: 8 项
              - generic [ref=e132]:
                - generic [ref=e133]:
                  - generic [ref=e134]:
                    - generic [ref=e135]: 咨询对象
                    - button "更正" [ref=e136] [cursor=pointer]
                  - paragraph [ref=e137]: 本人
                - generic [ref=e138]:
                  - generic [ref=e139]:
                    - generic [ref=e140]: 年龄
                    - button "更正" [ref=e141] [cursor=pointer]
                  - paragraph [ref=e142]: 32 岁
                - generic [ref=e143]:
                  - generic [ref=e144]:
                    - generic [ref=e145]: 主要问题
                    - button "更正" [ref=e146] [cursor=pointer]
                  - paragraph [ref=e147]: 手臂疼痛
                - generic [ref=e148]:
                  - generic [ref=e149]: 紧急信号筛查
                  - paragraph [ref=e151]: 用户否认上述紧急信号
                - generic [ref=e152]:
                  - generic [ref=e153]:
                    - generic [ref=e154]: 开始时间
                    - button "更正" [ref=e155] [cursor=pointer]
                  - paragraph [ref=e156]: 昨天
                - generic [ref=e157]:
                  - generic [ref=e158]:
                    - generic [ref=e159]: 变化趋势
                    - button "更正" [ref=e160] [cursor=pointer]
                  - paragraph [ref=e161]: 变化不大
                - generic [ref=e162]:
                  - generic [ref=e163]:
                    - generic [ref=e164]: 影响程度
                    - button "更正" [ref=e165] [cursor=pointer]
                  - paragraph [ref=e166]: "3"
                - generic [ref=e167]:
                  - generic [ref=e168]:
                    - generic [ref=e169]: 伴随不适
                    - button "更正" [ref=e170] [cursor=pointer]
                  - paragraph [ref=e171]: 明确否认
              - generic [ref=e172]:
                - generic [ref=e173]: 已提供
                - generic [ref=e175]: 待确认 / 未知
              - paragraph [ref=e177]: 记录可以更正。没有提到的信息，不会自动记为“没有”。
          - generic [ref=e178]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  1   | import { expect } from "@playwright/test";
  2   | 
  3   | export const medicationReport = "昨晚用过药物甲，用量不清楚";
  4   | export const allergyReport = "以前有过皮疹，是否药物过敏不确定";
  5   | 
  6   | async function snapshot(page, id) {
  7   |   return page.evaluate(async (sessionId) => {
  8   |     const response = await fetch(`/api/sessions/${sessionId}/snapshot`);
  9   |     if (!response.ok) throw new Error(`Snapshot failed: ${response.status}`);
  10  |     return response.json();
  11  |   }, id);
  12  | }
  13  | 
  14  | export async function exerciseUncertainReports(page, screenshot) {
  15  |   const errors = [];
  16  |   page.on("pageerror", (error) => errors.push(error.message));
  17  |   await expect(page.getByText("本机服务已连接")).toBeVisible();
  18  |   const created = page.waitForResponse(
  19  |     (response) =>
  20  |       response.request().method() === "POST" &&
  21  |       response.url().endsWith("/api/sessions"),
  22  |   );
  23  |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  24  |   const id = (await (await created).json()).session_id;
  25  |   const answers = {
  26  |     consult_for: "我自己",
  27  |     age: "32岁",
  28  |     chief_complaint: "手臂疼痛",
  29  |     emergency_screen: "都没有",
  30  |     onset: "昨天",
  31  |     trajectory: "变化不大",
  32  |     severity: "3分",
  33  |     associated_symptoms: "没有",
  34  |     medications_taken: medicationReport,
  35  |     allergies: allergyReport,
  36  |     relevant_history: "没有",
  37  |     patient_goal: "整理就医记录",
  38  |   };
  39  |   let data;
  40  |   for (let turn = 0; turn < 30; turn++) {
  41  |     data = await snapshot(page, id);
  42  |     if (["summary", "closed"].includes(data.state.phase)) break;
  43  |     if (data.state.active_task) {
> 44  |       await page.getByRole("button", { name: "暂时跳过", exact: true }).click();
      |                                                                     ^ Error: locator.click: Test timeout of 30000ms exceeded.
  45  |     } else {
  46  |       const text = answers[data.state.active_question];
  47  |       if (!text)
  48  |         throw new Error(`Unexpected question: ${data.state.active_question}`);
  49  |       await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  50  |       await page.getByRole("button", { name: "发送回答" }).click();
  51  |     }
  52  |     await expect(page.locator("#busy-indicator")).toBeHidden();
  53  |   }
  54  |   expect(data.state.phase).toBe("summary");
  55  |   const current = () => page.locator(".summary-card.current-summary");
  56  |   const report = (key) =>
  57  |     current().locator(`.unknown-report[data-fact-key='${key}']`);
  58  |   await expect(report("medications_taken").locator("blockquote")).toHaveText(
  59  |     medicationReport,
  60  |   );
  61  |   await expect(report("allergies").locator("blockquote")).toHaveText(
  62  |     allergyReport,
  63  |   );
  64  |   await expect(report("medications_taken")).toContainText("尚未确认");
  65  |   expect(data.state.facts.medications_taken.value).toBeNull();
  66  |   expect(data.state.facts.allergies.assertion).toBe("unknown");
  67  |   expect(
  68  |     await page.evaluate(
  69  |       () => document.documentElement.scrollWidth <= innerWidth,
  70  |     ),
  71  |   ).toBe(true);
  72  |   if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });
  73  | 
  74  |   // Start from the actual retained quote, keep the unresolved part, and confirm
  75  |   // that a correction replaces current evidence rather than reviving old text.
  76  |   await report("medications_taken")
  77  |     .getByRole("button", { name: "补充 / 更正", exact: true })
  78  |     .click();
  79  |   const editor = page.getByLabel("更正「用药与处理」");
  80  |   await expect(editor).toHaveValue(medicationReport);
  81  |   const updated = "昨晚用过药物甲，具体用量仍不清楚，准备带包装就医";
  82  |   await editor.fill(updated);
  83  |   await page.getByRole("button", { name: "保存并重新核对" }).click();
  84  |   await expect(page.locator("#edit-dialog")).toBeHidden();
  85  |   await expect(report("medications_taken").locator("blockquote")).toHaveText(
  86  |     updated,
  87  |   );
  88  |   data = await snapshot(page, id);
  89  |   const history = data.state.history.filter(
  90  |     (fact) => fact.key === "medications_taken",
  91  |   );
  92  |   expect(history.map((fact) => fact.evidence)).toEqual([
  93  |     medicationReport,
  94  |     updated,
  95  |   ]);
  96  |   expect(history[1].supersedes).toBe(history[0].fact_id);
  97  |   expect(history[1].assertion).toBe("unknown");
  98  |   expect(
  99  |     data.events.filter((event) => event.event_type === "model.requested"),
  100 |   ).toHaveLength(0);
  101 |   await page.getByRole("button", { name: "摘要准确", exact: true }).click();
  102 |   await expect(page.locator("#composer")).toBeHidden();
  103 |   await expect(page.locator(".summary-edit:enabled")).toHaveCount(0);
  104 |   await expect(page.locator(".summary-card").last()).toContainText(updated);
  105 |   expect(errors).toEqual([]);
  106 | }
  107 | 
```