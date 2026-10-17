# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: consultation-subjects.spec.mjs >> unsubmitted patient text stays with its original session
- Location: tests/ui/consultation-subjects.spec.mjs:7:1

# Error details

```
Error: expect(locator).toHaveValue(expected) failed

Locator:  getByRole('textbox', { name: '输入你的描述' })
Expected: ""
Received: "父亲昨晚用过药物甲，尚未发送。"
Timeout:  5000ms

Call log:
  - Expect "toHaveValue" getByRole('textbox', { name: '输入你的描述' }) with timeout 5000ms
  - waiting for getByRole('textbox', { name: '输入你的描述' })
    14 × locator resolved to <textarea rows="1" maxlength="2000" inputmode="text" id="message-input" placeholder="按自己的话说，也可以补充或更正…"></textarea>
       - unexpected value "父亲昨晚用过药物甲，尚未发送。"

```

```yaml
- textbox "输入你的描述":
  - /placeholder: 按自己的话说，也可以补充或更正…
  - text: 父亲昨晚用过药物甲，尚未发送。
```

# Test source

```ts
  1  | import { test, expect } from "@playwright/test";
  2  | import {
  3  |   createConsultationSubjectsModel,
  4  |   verifyConsultationSubjects,
  5  | } from "../fixtures/consultation-subjects.mjs";
  6  | 
  7  | test("unsubmitted patient text stays with its original session", async ({ page, request }) => {
  8  |   await request.post("/api/settings", { data: { backend: "demo" } });
  9  |   await page.goto("/");
  10 |   const create = async (button) => {
  11 |     const response = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/api/sessions"));
  12 |     await button.click();
  13 |     return (await response).json();
  14 |   };
  15 |   const first = await create(page.getByRole("button", { name: "开始问诊", exact: true }));
  16 |   const input = page.getByRole("textbox", { name: "输入你的描述" });
  17 |   await input.fill("父亲昨晚用过药物甲，尚未发送。");
  18 |   const second = await create(page.locator("#new-session"));
> 19 |   await expect(input).toHaveValue("");
     |                       ^ Error: expect(locator).toHaveValue(expected) failed
  20 |   await input.fill("母亲的另一条未发送描述。");
  21 |   await page.locator(`#recent-sessions .recent-item[data-session-id="${first.session_id}"]`).click();
  22 |   await expect(input).toHaveValue("父亲昨晚用过药物甲，尚未发送。");
  23 |   await page.locator(`#recent-sessions .recent-item[data-session-id="${second.session_id}"]`).click();
  24 |   await expect(input).toHaveValue("母亲的另一条未发送描述。");
  25 |   for (const id of [first.session_id, second.session_id]) {
  26 |     const snapshot = await (await request.get(`/api/sessions/${id}/snapshot`)).json();
  27 |     expect(snapshot.state.facts).toEqual({});
  28 |     expect(snapshot.events.filter(event => event.event_type === "turn.user_message")).toHaveLength(0);
  29 |   }
  30 | });
  31 | 
  32 | for (const mobile of [false, true])
  33 |   test(`consultation subjects stay separate and new-case text remains editable${mobile ? " on a narrow screen" : ""}`, async ({
  34 |     page,
  35 |     request,
  36 |   }) => {
  37 |     const model = await createConsultationSubjectsModel();
  38 |     try {
  39 |       expect(
  40 |         (
  41 |           await request.post("/api/settings", {
  42 |             data: {
  43 |               backend: "compatible",
  44 |               base_url: model.endpoint,
  45 |               model: "fixture",
  46 |               api_key: "subject-ui-only",
  47 |             },
  48 |           })
  49 |         ).ok(),
  50 |       ).toBe(true);
  51 |       await page.goto("/");
  52 |       await verifyConsultationSubjects(page, model, {
  53 |         shots: `docs/assets/ui/68-${mobile ? "mobile" : "wide"}-consultation-subject`,
  54 |         mobile,
  55 |       });
  56 |     } finally {
  57 |       await request.post("/api/settings", { data: { backend: "demo" } });
  58 |       await model.close();
  59 |     }
  60 |   });
  61 | 
```