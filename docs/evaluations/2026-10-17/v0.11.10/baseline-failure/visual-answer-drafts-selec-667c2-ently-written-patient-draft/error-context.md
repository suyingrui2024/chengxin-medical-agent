# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: visual-answer-drafts.spec.mjs >> selecting a visual degree preserves an independently written patient draft
- Location: tests/ui/visual-answer-drafts.spec.mjs:3:1

# Error details

```
Error: expect(locator).toHaveValue(expected) failed

Locator:  getByRole('textbox', { name: '输入你的描述' })
Expected: "昨晚用过药物甲，这段还没有发送。"
Received: "5分"
Timeout:  5000ms

Call log:
  - Expect "toHaveValue" getByRole('textbox', { name: '输入你的描述' }) with timeout 5000ms
  - waiting for getByRole('textbox', { name: '输入你的描述' })
    14 × locator resolved to <textarea rows="1" maxlength="2000" inputmode="text" id="message-input" placeholder="选一个分数，或描述生活受到的影响"></textarea>
       - unexpected value "5分"

```

```yaml
- textbox "输入你的描述":
  - /placeholder: 选一个分数，或描述生活受到的影响
  - text: 5分
```

# Test source

```ts
  1  | import { test, expect } from "@playwright/test";
  2  | 
  3  | test("selecting a visual degree preserves an independently written patient draft", async ({ page, request }) => {
  4  |   await request.post("/api/settings", { data: { backend: "demo" } });
  5  |   await page.goto("/");
  6  |   const created = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/api/sessions"));
  7  |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  8  |   const id = (await (await created).json()).session_id;
  9  |   const answer = async text => {
  10 |     await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  11 |     await page.getByRole("button", { name: "发送回答" }).click();
  12 |     await expect(page.locator("#send-button")).toBeEnabled();
  13 |   };
  14 |   await page.getByRole("button", { name: "我自己", exact: true }).click();
  15 |   await answer("32岁"); await answer("头疼");
  16 |   await page.getByRole("button", { name: "都没有", exact: true }).click();
  17 |   await answer("昨天逐渐出现");
  18 |   await page.getByRole("button", { name: "变化不大", exact: true }).click();
  19 |   const draft = "昨晚用过药物甲，这段还没有发送。";
  20 |   const before = await (await request.get(`/api/sessions/${id}/snapshot`)).json();
  21 |   await page.getByRole("textbox", { name: "输入你的描述" }).fill(draft);
  22 |   await page.getByRole("button", { name: "5 分", exact: true }).click();
> 23 |   await expect(page.getByRole("textbox", { name: "输入你的描述" })).toHaveValue(draft);
     |                                                               ^ Error: expect(locator).toHaveValue(expected) failed
  24 |   const selected = await (await request.get(`/api/sessions/${id}/snapshot`)).json();
  25 |   expect(selected.state.facts).toEqual(before.state.facts);
  26 |   expect(selected.events).toEqual(before.events);
  27 | });
  28 | 
```