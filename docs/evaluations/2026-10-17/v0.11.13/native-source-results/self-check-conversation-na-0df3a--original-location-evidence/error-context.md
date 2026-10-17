# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: self-check-conversation.spec.mjs >> native self-check conversation retains drafts and confirms original location evidence
- Location: tests/desktop/self-check-conversation.spec.mjs:14:1

# Error details

```
TypeError: apiRequestContext.get: Invalid URL
```

# Test source

```ts
  1   | import { createServer } from "node:http";
  2   | import { once } from "node:events";
  3   | import { expect } from "@playwright/test";
  4   | 
  5   | export async function createSelfCheckConversationModel(option = "head") {
  6   |   const record = (facts) => ({ tool: "record_facts", arguments: { facts } });
  7   |   const fact = (key, value, evidence, assertion = "present") => ({
  8   |     key,
  9   |     value,
  10  |     evidence,
  11  |     assertion,
  12  |   });
  13  |   const ask = { tool: "ask_question", arguments: { question_id: "onset" } };
  14  |   const commands = [
  15  |     record([
  16  |       fact("consult_for", "self", "我自己"),
  17  |       fact("age_years", 32, "32岁"),
  18  |       fact("chief_complaint", "头疼", "头疼"),
  19  |       fact("emergency_screen", null, "上述危险信号都没有", "absent"),
  20  |     ]),
  21  |     { tool: "show_self_check", arguments: { task_id: "body_location" } },
  22  |     record([fact("medications_taken", "昨晚用过药物甲", "昨晚用过药物甲")]),
  23  |     ask,
  24  |     {
  25  |       tool: "propose_self_check_result",
  26  |       arguments: {
  27  |         task_id: "body_location",
  28  |         option,
  29  |         evidence: option === "other" ? "是在其他部位" : "右太阳穴附近疼",
  30  |       },
  31  |     },
  32  |     ask,
  33  |   ];
  34  |   const requests = [];
  35  |   const server = createServer(async (request, response) => {
  36  |     let raw = "";
  37  |     for await (const chunk of request) raw += chunk;
  38  |     requests.push(JSON.parse(raw));
  39  |     const command = commands.shift();
  40  |     response.writeHead(command ? 200 : 500, {
  41  |       "Content-Type": "application/json",
  42  |     });
  43  |     response.end(
  44  |       JSON.stringify(
  45  |         command
  46  |           ? {
  47  |               choices: [
  48  |                 {
  49  |                   message: {
  50  |                     role: "assistant",
  51  |                     content: JSON.stringify(command),
  52  |                   },
  53  |                   finish_reason: "stop",
  54  |                 },
  55  |               ],
  56  |             }
  57  |           : { error: "Fixture exhausted" },
  58  |       ),
  59  |     );
  60  |   });
  61  |   server.listen(0, "127.0.0.1");
  62  |   await once(server, "listening");
  63  |   return {
  64  |     endpoint: `http://127.0.0.1:${server.address().port}/v1`,
  65  |     requests,
  66  |     option,
  67  |     async close() {
  68  |       server.closeAllConnections();
  69  |       await new Promise((resolve) => server.close(resolve));
  70  |     },
  71  |   };
  72  | }
  73  | 
  74  | export async function verifySelfCheckConversation(
  75  |   page,
  76  |   model,
  77  |   { shot, manual = false } = {},
  78  | ) {
  79  |   const errors = [];
  80  |   page.on("pageerror", (error) => errors.push(error.message));
  81  |   const created = page.waitForResponse(
  82  |     (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  83  |   );
  84  |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  85  |   const id = (await (await created).json()).session_id;
  86  |   const snapshot = async () =>
> 87  |     (await page.request.get(`/api/sessions/${id}/snapshot`)).json();
      |                         ^ TypeError: apiRequestContext.get: Invalid URL
  88  |   const input = page.getByRole("textbox", { name: "输入你的描述" });
  89  |   const send = async (text) => {
  90  |     await input.fill(text);
  91  |     await page.getByRole("button", { name: "发送回答" }).click();
  92  |     await expect(page.locator("#send-button")).toBeEnabled();
  93  |   };
  94  |   await send("我自己32岁，头疼；上述危险信号都没有。");
  95  |   await expect(page.locator("#active-task .body-map")).toBeVisible();
  96  |   const offered = await snapshot();
  97  |   const task = offered.events
  98  |     .filter((e) => e.event_type === "turn.agent_reply")
  99  |     .at(-1).payload.reply.task;
  100 |   await page
  101 |     .locator(".body-map")
  102 |     .getByRole("button", { name: "四肢", exact: true })
  103 |     .click();
  104 |   await page.locator(".location-step-guide > summary").click();
  105 |   await page
  106 |     .locator("#active-task")
  107 |     .getByRole("button", { name: "下一步", exact: true })
  108 |     .click();
  109 |   await send("昨晚用过药物甲。");
  110 |   await expect(page.locator("#active-question h2")).toBeVisible();
  111 |   const paused = await snapshot();
  112 |   expect(paused.state.facts.symptom_location).toBeUndefined();
  113 |   expect(paused.state.facts.medications_taken.value).toBe("昨晚用过药物甲");
  114 |   expect(paused.state.suspended_self_check.interaction_id).toBe(
  115 |     task.interaction_id,
  116 |   );
  117 |   const calls = model.requests.length;
  118 |   await page.getByRole("button", { name: /^继续指出最明显/ }).click();
  119 |   await expect(
  120 |     page.getByRole("button", { name: "记录：四肢", exact: true }),
  121 |   ).toBeEnabled();
  122 |   await expect(page.locator("#active-task .step-progress > span")).toHaveText(
  123 |     "2 / 2",
  124 |   );
  125 |   expect(model.requests).toHaveLength(calls);
  126 |   const raw = model.option === "other" ? "是在其他部位" : "右太阳穴附近疼";
  127 |   await send(raw);
  128 |   const proposal = page.locator("#active-task .body-proposal");
  129 |   await expect(proposal).toHaveText(`请核对部位 · “${raw}”`);
  130 |   const before = await snapshot();
  131 |   expect(before.state.facts.symptom_location).toBeUndefined();
  132 |   const evidenceTurn = before.events
  133 |     .filter((e) => e.event_type === "turn.user_message")
  134 |     .at(-1).turn_id;
  135 |   const label = model.option === "other" ? "其他部位" : "头/面部";
  136 |   let confirm = page.getByRole("button", {
  137 |     name: `核对并记录：${label}`,
  138 |     exact: true,
  139 |   });
  140 |   await expect(confirm).toBeEnabled();
  141 |   const geometries = await page.evaluate(() => {
  142 |     const pane = document.querySelector("#active-task").getBoundingClientRect();
  143 |     return [
  144 |       "#active-task h3",
  145 |       ".body-proposal",
  146 |       ".body-map-canvas",
  147 |       ".map-confirm",
  148 |       "#message-input",
  149 |       "#urgent-help",
  150 |     ].map((selector) => {
  151 |       const element = document.querySelector(selector);
  152 |       const rect = element.getBoundingClientRect();
  153 |       return {
  154 |         selector,
  155 |         top: rect.top,
  156 |         bottom: rect.bottom,
  157 |         visible:
  158 |           rect.top >= 0 &&
  159 |           rect.bottom <= innerHeight &&
  160 |           (selector === "#message-input" ||
  161 |             selector === "#urgent-help" ||
  162 |             (rect.top >= pane.top && rect.bottom <= pane.bottom)),
  163 |       };
  164 |     });
  165 |   });
  166 |   expect(geometries.filter((item) => !item.visible)).toEqual([]);
  167 |   if (manual) {
  168 |     await page.getByRole("button", { name: "修改部位", exact: true }).click();
  169 |     await page
  170 |       .locator(".body-map")
  171 |       .getByRole("button", { name: "胸部", exact: true })
  172 |       .click();
  173 |     confirm = page.getByRole("button", { name: "记录：胸部", exact: true });
  174 |   }
  175 |   const draft = "这段过敏补充尚未发送。";
  176 |   await input.fill(draft);
  177 |   await confirm.scrollIntoViewIfNeeded();
  178 |   if (shot) await page.screenshot({ path: shot, fullPage: true });
  179 |   await confirm.evaluate((node) => {
  180 |     window.oldConversationConfirm = node;
  181 |   });
  182 |   await confirm.click();
  183 |   await expect(page.locator("#send-button")).toBeEnabled();
  184 |   const after = await snapshot();
  185 |   const result = after.state.facts.symptom_location;
  186 |   expect(result.value).toBe(manual ? "胸部" : label);
  187 |   expect(result.evidence).toBe(manual ? "胸部" : raw);
```