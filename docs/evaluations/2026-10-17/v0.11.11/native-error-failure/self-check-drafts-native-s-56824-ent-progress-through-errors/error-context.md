# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: self-check-drafts.spec.mjs >> native self-check drafts restore selections and keep measurement progress through errors
- Location: tests/desktop/self-check-drafts.spec.mjs:13:1

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: true
Received: false
```

# Test source

```ts
  55  |     "2 / 2",
  56  |   );
  57  |   await page.getByRole("button", { name: "展开对话记录", exact: true }).click();
  58  |   await page.getByRole("button", { name: "返回当前任务", exact: true }).click();
  59  |   await expect(
  60  |     page.getByRole("button", { name: "记录：四肢", exact: true }),
  61  |   ).toBeEnabled();
  62  |   await input.fill(draft);
  63  |   const before = await degreeSnapshot(page, id);
  64  |   expect(before.state.facts.symptom_location).toBeUndefined();
  65  |   const created = page.waitForResponse(
  66  |     (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  67  |   );
  68  |   if (await page.locator("#mobile-menu").isVisible())
  69  |     await page.locator("#mobile-menu").click();
  70  |   await page.locator("#new-session").click();
  71  |   const otherId = (await (await created).json()).session_id;
  72  |   expect((await degreeSnapshot(page, otherId)).state.facts).toEqual({});
  73  |   if (await page.locator("#mobile-menu").isVisible())
  74  |     await page.locator("#mobile-menu").click();
  75  |   await page.locator(`#recent-sessions [data-session-id="${id}"]`).click();
  76  |   await expect(input).toHaveValue(draft);
  77  |   const confirm = page.getByRole("button", { name: "记录：四肢", exact: true });
  78  |   await expect(confirm).toBeEnabled();
  79  |   await expect(page.locator("#active-task .step-progress > span")).toHaveText(
  80  |     "2 / 2",
  81  |   );
  82  |   const restored = await degreeSnapshot(page, id);
  83  |   expect(restored.events).toEqual(before.events);
  84  |   expect(restored.state.facts).toEqual(before.state.facts);
  85  |   await confirm.scrollIntoViewIfNeeded();
  86  |   if (shot) await page.screenshot({ path: shot, fullPage: true });
  87  |   await confirm.evaluate((node) => {
  88  |     window.oldLocationConfirm = node;
  89  |   });
  90  |   const response = page.waitForResponse(
  91  |     (r) =>
  92  |       r.request().method() === "POST" &&
  93  |       r.url().endsWith(`/sessions/${id}/messages`),
  94  |   );
  95  |   await confirm.click();
  96  |   const result = await (await response).json();
  97  |   expect(result.state.facts.symptom_location.value).toBe("四肢");
  98  |   expect(result.state.facts.symptom_location.quality).toBe("not_assessed");
  99  |   await expect(input).toHaveValue(draft);
  100 |   const completed = await degreeSnapshot(page, id);
  101 |   expect(
  102 |     completed.events
  103 |       .slice(before.events.length)
  104 |       .filter((e) => e.event_type === "turn.user_message"),
  105 |   ).toHaveLength(1);
  106 |   await page.evaluate(() => {
  107 |     window.oldLocationConfirm.disabled = false;
  108 |     window.oldLocationConfirm.click();
  109 |   });
  110 |   expect((await degreeSnapshot(page, id)).events).toEqual(completed.events);
  111 | }
  112 | 
  113 | export async function verifyTemperatureProgress(page, { shot } = {}) {
  114 |   const id = await reachSelfCheck(page, "发热");
  115 |   await page
  116 |     .locator("#active-task")
  117 |     .getByRole("button", { name: "下一步", exact: true })
  118 |     .click();
  119 |   await page
  120 |     .locator("#active-task")
  121 |     .getByRole("button", { name: "下一步", exact: true })
  122 |     .click();
  123 |   await expect(page.locator("#active-task .step-progress > span")).toHaveText(
  124 |     "3 / 3",
  125 |   );
  126 |   const before = await degreeSnapshot(page, id);
  127 |   const offered = before.events
  128 |     .filter((e) => e.event_type === "turn.agent_reply")
  129 |     .at(-1).payload.reply;
  130 |   await page.getByRole("textbox", { name: "输入你的描述" }).fill("abc");
  131 |   await page.getByRole("button", { name: "发送回答" }).click();
  132 |   await expect(page.locator("#active-task .task-feedback")).toBeVisible();
  133 |   await expect(page.locator("#active-task .step-progress > span")).toHaveText(
  134 |     "3 / 3",
  135 |   );
  136 |   const invalid = await degreeSnapshot(page, id);
  137 |   const reply = invalid.events
  138 |     .filter((e) => e.event_type === "turn.agent_reply")
  139 |     .at(-1).payload.reply;
  140 |   expect(reply.action_id).not.toBe(offered.action_id);
  141 |   expect(reply.task.interaction_id).toBe(offered.task.interaction_id);
  142 |   expect(invalid.state.facts).toEqual(before.state.facts);
  143 |   const feedbackVisible = await page.evaluate(() => {
  144 |     const pane = document.querySelector("#active-task").getBoundingClientRect();
  145 |     const message = document
  146 |       .querySelector("#active-task .task-feedback")
  147 |       .getBoundingClientRect();
  148 |     return (
  149 |       message.top >= pane.top &&
  150 |       message.bottom <= pane.bottom &&
  151 |       message.bottom <= innerHeight
  152 |     );
  153 |   });
  154 |   if (shot) await page.screenshot({ path: shot, fullPage: true });
> 155 |   expect(feedbackVisible).toBe(true);
      |                           ^ Error: expect(received).toBe(expected) // Object.is equality
  156 |   if (false && shot) await page.screenshot({ path: shot, fullPage: true });
  157 | }
  158 | 
```