# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: repeated-observations.spec.mjs >> repeated temperatures retain history, distinguish skipped attempts and reassess risk on a narrow screen
- Location: tests/ui/repeated-observations.spec.mjs:8:3

# Error details

```
Error: expect(locator).toHaveText(expected) failed

Locator: locator('.summary-card.current-summary').locator('.observation-card').locator('.observation-value')
Expected: "38.25 ℃"
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toHaveText" locator('.summary-card.current-summary').locator('.observation-card').locator('.observation-value') with timeout 5000ms
  - waiting for locator('.summary-card.current-summary').locator('.observation-card').locator('.observation-value')

```

```yaml
- link "跳到主要内容":
  - /url: "#main"
- banner:
  - button "打开导航":
    - img
  - strong: 问诊
  - text: 本机服务已连接 直接模型
- main:
  - region "问诊":
    - heading "本次问诊" [level=1]
    - button "问诊记录 16"
    - region "问诊对话":
      - strong: 澄心问诊助手
      - text: 直接模型 · 本次会谈
      - button "紧急求助":
        - img
        - text: 紧急求助
      - log "会谈消息":
        - text: 你好，这次是你自己还是替家人咨询？请先说说最困扰的问题，以及大约从什么时候开始。 你 我自己32岁，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。 为了把当前情况记录得更清楚，可以完成下面这一步。做不到或不想做都可以跳过。
        - paragraph: 已提供图示任务：测一次腋下体温
        - text: 你 腋温38.25℃ 从开始到现在，总体是在加重、减轻，还是变化不大？ 你 总体变化不大；不适程度3分；没有其他不适；没有气促，能完整说话；干咳，没有咳血；能正常喝水吃饭，排尿正常；没有用药；过敏不知道；没有相关既往病史；想整理信息去就医。 这一步的对话服务暂时不可用，已保存的内容会保留。请稍后重试，或先查看目前记录。
      - button "重试这一轮"
      - text: 可以按自己的话回答，也可以随时补充或更正。 输入你的描述
      - textbox "输入你的描述":
        - /placeholder: 按自己的话说，也可以补充或更正…
      - button "发送回答":
        - img
      - paragraph: 0 / 2000
    - img
    - text: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  9   | const fact = (key, value, evidence, assertion = "present") => ({
  10  |   key,
  11  |   value,
  12  |   evidence,
  13  |   assertion,
  14  | });
  15  | const command = (tool, args = {}) => ({ tool, arguments: args });
  16  | 
  17  | export async function createRepeatedObservationModel() {
  18  |   const requests = [];
  19  |   const commands = [
  20  |     command("record_facts", {
  21  |       facts: [
  22  |         fact("consult_for", "self", "我自己"),
  23  |         fact("age_years", 32, "32岁"),
  24  |         fact("chief_complaint", "咳嗽发烧", "咳嗽发烧"),
  25  |         fact("onset", "两天", "两天"),
  26  |         fact("emergency_screen", null, "上述危险信号都没有", "absent"),
  27  |       ],
  28  |     }),
  29  |     command("show_self_check", { task_id: "axillary_temperature" }),
  30  |     command("ask_question", { question_id: "trajectory" }),
  31  |     command("record_facts", {
  32  |       facts: [
  33  |         fact("trajectory", "stable", "变化不大"),
  34  |         fact("severity", 3, "不适程度3分"),
  35  |         fact("associated_symptoms", null, "没有其他不适", "absent"),
  36  |         fact("resp_breathing", "没有气促，能完整说话", "没有气促，能完整说话"),
  37  |         fact("resp_cough_details", "干咳，没有咳血", "干咳，没有咳血"),
  38  |         fact(
  39  |           "resp_intake",
  40  |           "能正常喝水吃饭，排尿正常",
  41  |           "能正常喝水吃饭，排尿正常",
  42  |         ),
  43  |         fact("medications_taken", null, "没有用药", "absent"),
  44  |         fact("allergies", null, "过敏不知道", "unknown"),
  45  |         fact("relevant_history", null, "没有相关既往病史", "absent"),
  46  |         fact("patient_goal", "整理信息去就医", "想整理信息去就医"),
  47  |       ],
  48  |     }),
  49  |   ];
  50  |   const server = createServer(async (request, response) => {
  51  |     let raw = "";
  52  |     for await (const chunk of request) raw += chunk;
  53  |     requests.push(JSON.parse(raw));
  54  |     const next = commands.shift();
  55  |     response.writeHead(next ? 200 : 500, {
  56  |       "Content-Type": "application/json",
  57  |     });
  58  |     response.end(
  59  |       JSON.stringify(
  60  |         next
  61  |           ? {
  62  |               choices: [
  63  |                 {
  64  |                   message: { role: "assistant", content: JSON.stringify(next) },
  65  |                   finish_reason: "stop",
  66  |                 },
  67  |               ],
  68  |             }
  69  |           : { error: "Unexpected model request" },
  70  |       ),
  71  |     );
  72  |   });
  73  |   server.listen(0, "127.0.0.1");
  74  |   await once(server, "listening");
  75  |   return {
  76  |     endpoint: `http://127.0.0.1:${server.address().port}/v1`,
  77  |     requests,
  78  |     async close() {
  79  |       server.closeAllConnections();
  80  |       await new Promise((resolve) => server.close(resolve));
  81  |     },
  82  |   };
  83  | }
  84  | 
  85  | export async function verifyRepeatedObservations(
  86  |   page,
  87  |   model,
  88  |   screenshotPrefix,
  89  | ) {
  90  |   await expect(page.getByText("本机服务已连接")).toBeVisible();
  91  |   const created = page.waitForResponse(
  92  |     (response) =>
  93  |       response.request().method() === "POST" &&
  94  |       response.url().endsWith("/api/sessions"),
  95  |   );
  96  |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  97  |   const id = (await (await created).json()).session_id;
  98  |   const send = async (text) => {
  99  |     await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  100 |     await page.getByRole("button", { name: "发送回答" }).click();
  101 |   };
  102 |   const current = () => page.locator(".summary-card.current-summary");
  103 |   const series = () => current().locator(".observation-card");
  104 |   await send(opening);
  105 |   await expect(page.locator("#active-task .structured-task")).toBeVisible();
  106 |   await send("腋温38.25℃");
  107 |   await expect(page.locator("#active-question")).toBeVisible();
  108 |   await send(details);
> 109 |   await expect(series().locator(".observation-value")).toHaveText("38.25 ℃");
      |                                                        ^ Error: expect(locator).toHaveText(expected) failed
  110 |   expect(model.requests).toHaveLength(4);
  111 |   const oldSnapshot = await page.evaluate(
  112 |     async (sessionId) =>
  113 |       (await fetch(`/api/sessions/${sessionId}/snapshot`)).json(),
  114 |     id,
  115 |   );
  116 |   const oldId = oldSnapshot.state.facts.temperature_c.fact_id;
  117 |   await series().getByRole("button", { name: "再测一次", exact: true }).click();
  118 |   await expect(
  119 |     page.locator("#active-task .observation-previous"),
  120 |   ).toContainText("38.25 ℃");
  121 |   await expect(page.locator("#active-task .step-progress")).toContainText(
  122 |     "1 / 3",
  123 |   );
  124 |   await send("腋温37.6℃");
  125 |   await expect(series().locator(".observation-value")).toHaveText("37.6 ℃");
  126 |   await expect(
  127 |     current().locator(".summary-item[data-fact-key='temperature_c']"),
  128 |   ).toHaveCount(0);
  129 |   await series().locator(".observation-details > summary").click();
  130 |   await expect(series().locator(".observation-history li")).toHaveCount(2);
  131 |   await expect(series().locator(".observation-history")).toContainText(
  132 |     "38.25 ℃",
  133 |   );
  134 |   await expect(series()).toContainText("系统记录时间");
  135 |   if (screenshotPrefix)
  136 |     await page.screenshot({
  137 |       path: `${screenshotPrefix}-history.png`,
  138 |       fullPage: true,
  139 |     });
  140 |   await series().getByRole("button", { name: "再测一次", exact: true }).click();
  141 |   await send("38.6还是39.2");
  142 |   await expect(page.locator("#active-task .task-feedback")).toBeVisible();
  143 |   await page.getByRole("button", { name: "暂时跳过", exact: true }).click();
  144 |   await expect(series().locator(".observation-value")).toHaveText("37.6 ℃");
  145 |   await expect(series()).toContainText("上次记录 · 本次未完成");
  146 |   await series().locator(".observation-details > summary").click();
  147 |   await expect(series().locator(".observation-history")).toContainText(
  148 |     "本次跳过",
  149 |   );
  150 |   if (screenshotPrefix)
  151 |     await page.screenshot({
  152 |       path: `${screenshotPrefix}-skipped.png`,
  153 |       fullPage: true,
  154 |     });
  155 |   await send("我刚刚又测了一次腋温40.1℃，想重新测量。");
  156 |   await expect(
  157 |     page.locator("#active-task .observation-proposal .observation-value"),
  158 |   ).toHaveText("40.1 ℃");
  159 |   await expect(
  160 |     page.locator("#active-task .observation-previous"),
  161 |   ).toContainText("37.6 ℃");
  162 |   expect(
  163 |     await page.evaluate(
  164 |       () => document.documentElement.scrollWidth <= innerWidth,
  165 |     ),
  166 |   ).toBe(true);
  167 |   if (screenshotPrefix)
  168 |     await page.screenshot({
  169 |       path: `${screenshotPrefix}-candidate.png`,
  170 |       fullPage: true,
  171 |     });
  172 |   await page
  173 |     .getByRole("button", { name: "核对并记录 40.1℃", exact: true })
  174 |     .click();
  175 |   await expect(series().locator(".observation-value")).toHaveText("40.1 ℃");
  176 |   await expect(current().locator(".observation-repeat")).toHaveCount(0);
  177 |   await expect(current().locator(".summary-route")).toContainText(
  178 |     "今天联系医疗机构",
  179 |   );
  180 |   const snapshot = await page.evaluate(
  181 |     async (sessionId) =>
  182 |       (await fetch(`/api/sessions/${sessionId}/snapshot`)).json(),
  183 |     id,
  184 |   );
  185 |   expect(snapshot.state.facts.temperature_c.value).toBe(40.1);
  186 |   const readings = snapshot.state.history.filter(
  187 |     (fact) => fact.key === "temperature_c",
  188 |   );
  189 |   expect(readings.map((fact) => fact.value)).toEqual([38.25, 37.6, 40.1]);
  190 |   expect(readings[1].supersedes).toBe(oldId);
  191 |   expect(
  192 |     snapshot.state.observation_attempts.map((item) => item.status),
  193 |   ).toEqual(["recorded", "skipped", "recorded"]);
  194 |   expect(model.requests).toHaveLength(4);
  195 |   expect(
  196 |     snapshot.state.observation_attempts.every((item) => item.finished_at),
  197 |   ).toBe(true);
  198 |   expect(
  199 |     await page.evaluate(
  200 |       () => document.documentElement.scrollWidth <= innerWidth,
  201 |     ),
  202 |   ).toBe(true);
  203 | }
  204 | 
```