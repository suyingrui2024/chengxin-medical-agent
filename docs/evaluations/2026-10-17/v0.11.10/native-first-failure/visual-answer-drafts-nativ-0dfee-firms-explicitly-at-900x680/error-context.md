# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: visual-answer-drafts.spec.mjs >> native degree selection preserves independent patient text and confirms explicitly at 900x680
- Location: tests/desktop/visual-answer-drafts.spec.mjs:11:1

# Error details

```
Error: expect(received).toEqual(expected) // deep equality

- Expected  - 1
+ Received  + 6

- Array []
+ Array [
+   Object {
+     "selector": "#active-question h2",
+     "visible": false,
+   },
+ ]
```

# Test source

```ts
  1   | import { expect } from "@playwright/test";
  2   | 
  3   | export async function degreeSnapshot(page, id) {
  4   |   return page.evaluate(
  5   |     async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
  6   |     id,
  7   |   );
  8   | }
  9   | 
  10  | export async function reachDegree(page) {
  11  |   const created = page.waitForResponse(
  12  |     (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  13  |   );
  14  |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  15  |   const id = (await (await created).json()).session_id;
  16  |   const answer = async (text) => {
  17  |     await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  18  |     await page.getByRole("button", { name: "发送回答" }).click();
  19  |     await expect(page.locator("#send-button")).toBeEnabled();
  20  |   };
  21  |   await page.getByRole("button", { name: "我自己", exact: true }).click();
  22  |   await answer("32岁");
  23  |   await answer("头疼");
  24  |   await page.getByRole("button", { name: "都没有", exact: true }).click();
  25  |   await answer("昨天逐渐出现");
  26  |   await page.getByRole("button", { name: "变化不大", exact: true }).click();
  27  |   await expect(page.locator("#active-question .severity-scale")).toBeVisible();
  28  |   await expect(
  29  |     page.getByRole("button", { name: "先选择分数", exact: true }),
  30  |   ).toBeDisabled();
  31  |   return id;
  32  | }
  33  | 
  34  | export async function verifyDegreeDraft(page, { shot } = {}) {
  35  |   const errors = [];
  36  |   page.on("pageerror", (error) => errors.push(error.message));
  37  |   const id = await reachDegree(page);
  38  |   const input = page.getByRole("textbox", { name: "输入你的描述" });
  39  |   const draft = "昨晚用过药物甲，这段还没有发送。";
  40  |   const before = await degreeSnapshot(page, id);
  41  |   await input.fill(draft);
  42  |   for (const degree of [0, 10, 5]) {
  43  |     const button = page.getByRole("button", {
  44  |       name: `${degree} 分`,
  45  |       exact: true,
  46  |     });
  47  |     await button.focus();
  48  |     await page.keyboard.press("Space");
  49  |     await expect(page.locator(".severity-value")).toHaveText(`${degree} 分`);
  50  |     await expect(input).toHaveValue(draft);
  51  |     await expect(
  52  |       page.getByRole("button", { name: `确认 ${degree} 分`, exact: true }),
  53  |     ).toBeEnabled();
  54  |   }
  55  |   const selected = await degreeSnapshot(page, id);
  56  |   expect(selected.state.facts).toEqual(before.state.facts);
  57  |   expect(selected.events).toEqual(before.events);
  58  |   if (shot) await page.screenshot({ path: shot, fullPage: true });
  59  |   const geometry = await page.evaluate(() => {
  60  |     const pane = document
  61  |       .querySelector("#active-question")
  62  |       .getBoundingClientRect();
  63  |     const selectors = [
  64  |       "#active-question h2",
  65  |       ".severity-options",
  66  |       ".severity-confirm",
  67  |       "#message-input",
  68  |     ];
  69  |     return selectors.map((selector) => {
  70  |       const rect = document.querySelector(selector).getBoundingClientRect();
  71  |       return {
  72  |         selector,
  73  |         visible:
  74  |           rect.top >= 0 &&
  75  |           rect.bottom <= innerHeight &&
  76  |           rect.left >= 0 &&
  77  |           rect.right <= innerWidth &&
  78  |           (selector === "#message-input" ||
  79  |             (rect.top >= pane.top && rect.bottom <= pane.bottom)),
  80  |       };
  81  |     });
  82  |   });
> 83  |   expect(geometry.filter((item) => !item.visible)).toEqual([]);
      |                                                    ^ Error: expect(received).toEqual(expected) // deep equality
  84  |   await page.locator(".severity-confirm").evaluate((node) => {
  85  |     window.oldDegreeConfirm = node;
  86  |   });
  87  |   const response = page.waitForResponse(
  88  |     (r) =>
  89  |       r.request().method() === "POST" &&
  90  |       r.url().endsWith(`/sessions/${id}/messages`),
  91  |   );
  92  |   await page.getByRole("button", { name: "确认 5 分", exact: true }).click();
  93  |   const result = await (await response).json();
  94  |   expect(result.state.facts.severity.value).toBe(5);
  95  |   expect(result.state.facts.medications_taken).toBeUndefined();
  96  |   await expect(page.locator(".severity-scale")).toHaveCount(0);
  97  |   await expect(input).toHaveValue(draft);
  98  |   const after = await degreeSnapshot(page, id);
  99  |   const added = after.events
  100 |     .slice(before.events.length)
  101 |     .filter((e) => e.event_type === "turn.user_message");
  102 |   expect(added).toHaveLength(1);
  103 |   expect(added[0].payload.text).toBe("5分");
  104 |   await page.evaluate(() => {
  105 |     window.oldDegreeConfirm.disabled = false;
  106 |     window.oldDegreeConfirm.click();
  107 |   });
  108 |   const stale = await degreeSnapshot(page, id);
  109 |   expect(stale.events).toEqual(after.events);
  110 |   expect(stale.state.facts).toEqual(after.state.facts);
  111 |   expect(errors).toEqual([]);
  112 | }
  113 | 
```