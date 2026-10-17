# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: self-check-drafts.spec.mjs >> unconfirmed location and steps survive switching sessions on mobile
- Location: tests/ui/self-check-drafts.spec.mjs:10:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: page.waitForResponse: Test timeout of 30000ms exceeded.
```

# Page snapshot

```yaml
- generic [ref=e1]:
  - link "跳到主要内容" [ref=e2] [cursor=pointer]:
    - /url: "#main"
  - generic [ref=e3]:
    - text: · · ·
    - generic [ref=e4]:
      - banner [ref=e5]:
        - generic [ref=e6]:
          - button "打开导航" [ref=e7] [cursor=pointer]
          - strong [ref=e10]: 问诊
        - generic [ref=e11]:
          - generic [ref=e12]: 本机服务已连接
          - generic [ref=e14]: 离线演示
      - main [ref=e15]:
        - region "问诊" [ref=e16]:
          - generic [ref=e17]:
            - heading "观察与记录" [level=1] [ref=e19]
            - button "问诊记录 8" [ref=e20] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e21]: "8"
          - region "问诊对话" [ref=e23]:
            - generic [ref=e24]:
              - generic [ref=e27]:
                - strong [ref=e28]: 澄心问诊助手
                - generic "按用户原话报告的就诊对象，未核验身份。" [ref=e29]: 本人 · 本次问诊 · 离线演示
              - button "紧急求助" [ref=e31] [cursor=pointer]
            - generic [ref=e34]:
              - status [ref=e35]: 按图观察 · 可以跳过
              - button "展开对话记录" [ref=e36] [cursor=pointer]
            - generic "当前图示任务" [ref=e37]:
              - article [ref=e38]:
                - generic [ref=e39]:
                  - heading "指出最明显的不适部位" [level=3] [ref=e41]
                  - generic [ref=e42]: 待临床审核
                - generic [ref=e43]:
                  - figure [ref=e44]:
                    - group "选择主观不适部位，不需要按压身体" [ref=e45]:
                      - generic [ref=e46]:
                        - img [aria-hidden] [ref=e47]:
                          - generic [ref=e51]:
                            - generic [ref=e52]: 正面
                            - generic [ref=e53]: 背面
                        - button "头/面部" [ref=e54] [cursor=pointer]
                        - button "胸部" [ref=e55] [cursor=pointer]
                        - button "腹部" [ref=e56] [cursor=pointer]
                        - button "背/腰部" [ref=e57] [cursor=pointer]
                        - button "四肢" [pressed] [ref=e58] [cursor=pointer]
                      - paragraph [ref=e59]: 已选择：四肢（主观位置）
                      - button "记录：四肢" [ref=e60] [cursor=pointer]
                  - generic [ref=e61]:
                    - generic [ref=e62]: 2 / 2
                    - paragraph [ref=e66]: 选择部位；需要时补充左侧、右侧、范围和是否移动。
                    - generic [ref=e67]:
                      - button "上一步" [ref=e68] [cursor=pointer]
                      - button "填写结果" [ref=e69] [cursor=pointer]
                - paragraph [ref=e70]: 停止 / 跳过：无法确定位置时可跳过
                - group [ref=e71]:
                  - generic "为什么观察 · 使用限制" [ref=e72] [cursor=pointer]
                - paragraph [ref=e73]: 选一个部位，或用自己的话描述位置。
            - generic [ref=e74]:
              - generic "快捷回答" [ref=e75]:
                - button "其他部位" [ref=e76] [cursor=pointer]
                - button "说不清/跳过" [ref=e77] [cursor=pointer]
              - generic [ref=e78]: 按图示完成后记录结果；做不到也可以跳过。
              - generic [ref=e79]:
                - generic [ref=e80]: 输入你的描述
                - textbox "输入你的描述" [active] [ref=e81]:
                  - /placeholder: 按自己的话说，也可以补充或更正…
                  - text: 右臂外侧，稍后再补充。
                - button "发送回答" [ref=e82] [cursor=pointer]
              - paragraph [ref=e85]:
                - generic [ref=e86]: 11 / 2000
          - generic [ref=e87]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  1   | import { expect } from "@playwright/test";
  2   | import { reachDegree, degreeSnapshot } from "./visual-answer-drafts.mjs";
  3   | 
  4   | export async function reachSelfCheck(page, complaint = "头疼") {
  5   |   const id = await reachDegree(page, { complaint });
  6   |   await page.getByRole("button", { name: "3 分", exact: true }).click();
  7   |   await page.getByRole("button", { name: "确认 3 分", exact: true }).click();
  8   |   await page.getByRole("textbox", { name: "输入你的描述" }).fill("没有");
  9   |   await page.getByRole("button", { name: "发送回答" }).click();
  10  |   await expect(page.locator("#active-task .task-card")).toBeVisible();
  11  |   return id;
  12  | }
  13  | 
  14  | export async function verifyLocationDraft(page, { shot } = {}) {
  15  |   const id = await reachSelfCheck(page);
  16  |   const input = page.getByRole("textbox", { name: "输入你的描述" });
  17  |   const draft = "右臂外侧，稍后再补充。";
  18  |   await page
  19  |     .locator(".body-map")
  20  |     .getByRole("button", { name: "四肢", exact: true })
  21  |     .click();
  22  |   await page
  23  |     .locator("#active-task")
  24  |     .getByRole("button", { name: "下一步", exact: true })
  25  |     .click();
  26  |   await expect(page.locator("#active-task .step-progress > span")).toHaveText(
  27  |     "2 / 2",
  28  |   );
  29  |   await input.fill(draft);
  30  |   const before = await degreeSnapshot(page, id);
  31  |   expect(before.state.facts.symptom_location).toBeUndefined();
> 32  |   const created = page.waitForResponse(
      |                        ^ Error: page.waitForResponse: Test timeout of 30000ms exceeded.
  33  |     (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  34  |   );
  35  |   await page.locator("#new-session").click();
  36  |   const otherId = (await (await created).json()).session_id;
  37  |   expect((await degreeSnapshot(page, otherId)).state.facts).toEqual({});
  38  |   await page.locator(`#recent-sessions [data-session-id="${id}"]`).click();
  39  |   await expect(input).toHaveValue(draft);
  40  |   const confirm = page.getByRole("button", { name: "记录：四肢", exact: true });
  41  |   await expect(confirm).toBeEnabled();
  42  |   await expect(page.locator("#active-task .step-progress > span")).toHaveText(
  43  |     "2 / 2",
  44  |   );
  45  |   const restored = await degreeSnapshot(page, id);
  46  |   expect(restored.events).toEqual(before.events);
  47  |   expect(restored.state.facts).toEqual(before.state.facts);
  48  |   await confirm.scrollIntoViewIfNeeded();
  49  |   if (shot) await page.screenshot({ path: shot, fullPage: true });
  50  |   await confirm.evaluate((node) => {
  51  |     window.oldLocationConfirm = node;
  52  |   });
  53  |   const response = page.waitForResponse(
  54  |     (r) =>
  55  |       r.request().method() === "POST" &&
  56  |       r.url().endsWith(`/sessions/${id}/messages`),
  57  |   );
  58  |   await confirm.click();
  59  |   const result = await (await response).json();
  60  |   expect(result.state.facts.symptom_location.value).toBe("四肢");
  61  |   expect(result.state.facts.symptom_location.quality).toBe("not_assessed");
  62  |   await expect(input).toHaveValue(draft);
  63  |   const completed = await degreeSnapshot(page, id);
  64  |   expect(
  65  |     completed.events
  66  |       .slice(before.events.length)
  67  |       .filter((e) => e.event_type === "turn.user_message"),
  68  |   ).toHaveLength(1);
  69  |   await page.evaluate(() => {
  70  |     window.oldLocationConfirm.disabled = false;
  71  |     window.oldLocationConfirm.click();
  72  |   });
  73  |   expect((await degreeSnapshot(page, id)).events).toEqual(completed.events);
  74  | }
  75  | 
  76  | export async function verifyTemperatureProgress(page, { shot } = {}) {
  77  |   const id = await reachSelfCheck(page, "发热");
  78  |   await page
  79  |     .locator("#active-task")
  80  |     .getByRole("button", { name: "下一步", exact: true })
  81  |     .click();
  82  |   await page
  83  |     .locator("#active-task")
  84  |     .getByRole("button", { name: "下一步", exact: true })
  85  |     .click();
  86  |   await expect(page.locator("#active-task .step-progress > span")).toHaveText(
  87  |     "3 / 3",
  88  |   );
  89  |   const before = await degreeSnapshot(page, id);
  90  |   const offered = before.events
  91  |     .filter((e) => e.event_type === "turn.agent_reply")
  92  |     .at(-1).payload.reply;
  93  |   await page.getByRole("textbox", { name: "输入你的描述" }).fill("abc");
  94  |   await page.getByRole("button", { name: "发送回答" }).click();
  95  |   await expect(page.locator("#active-task .task-feedback")).toBeVisible();
  96  |   await expect(page.locator("#active-task .step-progress > span")).toHaveText(
  97  |     "3 / 3",
  98  |   );
  99  |   const invalid = await degreeSnapshot(page, id);
  100 |   const reply = invalid.events
  101 |     .filter((e) => e.event_type === "turn.agent_reply")
  102 |     .at(-1).payload.reply;
  103 |   expect(reply.action_id).not.toBe(offered.action_id);
  104 |   expect(reply.task.interaction_id).toBe(offered.task.interaction_id);
  105 |   expect(invalid.state.facts).toEqual(before.state.facts);
  106 |   if (shot) await page.screenshot({ path: shot, fullPage: true });
  107 | }
  108 | 
```