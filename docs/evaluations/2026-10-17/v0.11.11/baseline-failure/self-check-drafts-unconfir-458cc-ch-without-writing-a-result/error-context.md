# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: self-check-drafts.spec.mjs >> unconfirmed body location survives a session switch without writing a result
- Location: tests/ui/self-check-drafts.spec.mjs:7:1

# Error details

```
Error: expect(locator).toBeEnabled() failed

Locator: getByRole('button', { name: '记录：四肢', exact: true })
Expected: enabled
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeEnabled" getByRole('button', { name: '记录：四肢', exact: true }) with timeout 5000ms
  - waiting for getByRole('button', { name: '记录：四肢', exact: true })

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
    - button "会谈记录 2":
      - img
      - text: 会谈记录 2
  - text: 最近问诊 2
  - button "·新的问诊"
  - button "·本人 · 头疼"
  - text: 本机工作空间 会谈仅保留到服务关闭 研究原型 能力协议 v1
- banner:
  - text: 工作空间 /
  - strong: 问诊
  - text: 本机服务已连接 离线演示
- main:
  - region "问诊":
    - heading "观察与记录" [level=1]
    - button "问诊记录 8":
      - img
      - text: 问诊记录 8
    - region "问诊对话":
      - strong: 澄心问诊助手
      - text: 本人 · 本次问诊 · 离线演示 定向观察
      - button "紧急求助":
        - img
        - text: 紧急求助
      - status: 按图观察 · 可以跳过
      - button "展开对话记录"
      - article:
        - heading "指出最明显的不适部位" [level=3]
        - text: 待临床审核
        - figure:
          - group "选择主观不适部位，不需要按压身体":
            - button "头/面部"
            - button "胸部"
            - button "腹部"
            - button "背/腰部"
            - button "四肢"
            - paragraph: 点击部位，再确认记录
            - button "先选择部位" [disabled]
        - text: 1 / 2
        - paragraph: 看图找到最明显的位置。
        - button "上一步" [disabled]
        - button "下一步"
        - paragraph: 停止 / 跳过：无法确定位置时可跳过
        - group: 为什么观察 · 使用限制
        - paragraph: 选一个部位，或用自己的话描述位置。
      - button "其他部位"
      - button "说不清/跳过"
      - text: 按图示完成后记录结果；做不到也可以跳过。 输入你的描述
      - textbox "输入你的描述":
        - /placeholder: 按自己的话说，也可以补充或更正…
        - text: 右臂外侧，稍后再补充。
      - button "发送回答":
        - img
      - paragraph: 11 / 2000
    - complementary "问诊记录":
      - text: 本次问诊 定向观察
      - strong: 记录一次定向观察
      - heading "已记录的信息" [level=2]
      - text: 8 项 咨询对象
      - button "更正"
      - paragraph: 本人
      - text: 年龄
      - button "更正"
      - paragraph: 32 岁
      - text: 主要问题
      - button "更正"
      - paragraph: 头疼
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
  1  | import { test, expect } from "@playwright/test";
  2  | import {
  3  |   reachDegree,
  4  |   degreeSnapshot,
  5  | } from "../fixtures/visual-answer-drafts.mjs";
  6  | 
  7  | test("unconfirmed body location survives a session switch without writing a result", async ({
  8  |   page,
  9  |   request,
  10 | }) => {
  11 |   await request.post("/api/settings", { data: { backend: "demo" } });
  12 |   await page.goto("/");
  13 |   const id = await reachDegree(page);
  14 |   await page.getByRole("button", { name: "3 分", exact: true }).click();
  15 |   await page.getByRole("button", { name: "确认 3 分", exact: true }).click();
  16 |   const input = page.getByRole("textbox", { name: "输入你的描述" });
  17 |   await input.fill("没有");
  18 |   await page.getByRole("button", { name: "发送回答" }).click();
  19 |   await expect(page.locator(".body-map")).toBeVisible();
  20 |   await page
  21 |     .locator(".body-map")
  22 |     .getByRole("button", { name: "四肢", exact: true })
  23 |     .click();
  24 |   await input.fill("右臂外侧，稍后再补充。");
  25 |   const before = await degreeSnapshot(page, id);
  26 |   const created = page.waitForResponse(
  27 |     (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  28 |   );
  29 |   await page.locator("#new-session").click();
  30 |   await created;
  31 |   await page.locator(`#recent-sessions [data-session-id="${id}"]`).click();
  32 |   await expect(input).toHaveValue("右臂外侧，稍后再补充。");
  33 |   await expect(
  34 |     page.getByRole("button", { name: "记录：四肢", exact: true }),
> 35 |   ).toBeEnabled();
     |     ^ Error: expect(locator).toBeEnabled() failed
  36 |   const restored = await degreeSnapshot(page, id);
  37 |   expect(restored.events).toEqual(before.events);
  38 |   expect(restored.state.facts).toEqual(before.state.facts);
  39 |   expect(restored.state.facts.symptom_location).toBeUndefined();
  40 | });
  41 | 
```