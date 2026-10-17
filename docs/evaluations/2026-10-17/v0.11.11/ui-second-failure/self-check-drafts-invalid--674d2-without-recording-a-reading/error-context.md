# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: self-check-drafts.spec.mjs >> invalid temperature feedback keeps the same task progress without recording a reading
- Location: tests/ui/self-check-drafts.spec.mjs:22:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('#active-task .task-card')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" locator('#active-task .task-card') with timeout 5000ms
  - waiting for locator('#active-task .task-card')

```

```yaml
- link "跳到主要内容":
  - /url: "#main"
- complementary "主导航":
  - link "澄心 CHENGXIN HEALTH":
    - /url: "#consultation"
    - strong: 澄心
    - text: CHENGXIN HEALTH
  - button "新的问诊 ↗":
    - img
    - text: 新的问诊 ↗
  - text: 界面模式
  - combobox "界面模式":
    - option "患者视图" [selected]
    - option "研发视图"
  - paragraph: 工作空间
  - navigation:
    - button "问诊":
      - img
      - text: 问诊
    - button "会谈记录 4":
      - img
      - text: 会谈记录 4
  - text: 最近问诊 4
  - button "·本人 · 发热"
  - button "·本人 · 头疼"
  - button "·新的问诊"
  - button "·本人 · 头疼"
  - text: 本机工作空间 会谈仅保留到服务关闭 研究原型 能力协议 v1
- banner:
  - text: 工作空间 /
  - strong: 问诊
  - text: 本机服务已连接 离线演示
- main:
  - region "问诊":
    - heading "本次问诊" [level=1]
    - button "问诊记录 8":
      - img
      - text: 问诊记录 8
    - region "问诊对话":
      - strong: 澄心问诊助手
      - text: 本人 · 本次问诊 · 离线演示 了解经过
      - button "紧急求助":
        - img
        - text: 紧急求助
      - status: 当前这一步
      - button "展开对话记录"
      - article:
        - heading "现在呼吸怎么样？" [level=2]
        - text: 气短 / 喘 说完整句话费力？ 静坐也呼吸困难？
        - group: 查看完整问题
      - text: 输入你的描述
      - textbox "输入你的描述":
        - /placeholder: 按自己的话说，也可以补充或更正…
      - button "发送回答":
        - img
      - paragraph: 0 / 2000
    - complementary "问诊记录":
      - text: 本次问诊 了解经过
      - strong: 了解症状经过
      - heading "已记录的信息" [level=2]
      - text: 8 项 咨询对象
      - button "更正"
      - paragraph: 本人
      - text: 年龄
      - button "更正"
      - paragraph: 32 岁
      - text: 主要问题
      - button "更正"
      - paragraph: 发热
      - text: 紧急信号筛查
      - paragraph: 用户否认上述紧急信号
      - text: 开始时间
      - button "更正"
      - paragraph: 昨天逐渐出现
      - text: 变化趋势
      - button "更正"
      - paragraph: 变化不大
      - text: 影响程度
      - button "更正"
      - paragraph: "3"
      - text: 伴随不适
      - button "更正"
      - paragraph: 明确否认
      - text: 已提供 待确认 / 未知
      - paragraph: 记录可以更正。没有提到的信息，不会自动记为“没有”。
    - img
    - text: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
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
> 10  |   await expect(page.locator("#active-task .task-card")).toBeVisible();
      |                                                         ^ Error: expect(locator).toBeVisible() failed
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
  32  |   const created = page.waitForResponse(
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