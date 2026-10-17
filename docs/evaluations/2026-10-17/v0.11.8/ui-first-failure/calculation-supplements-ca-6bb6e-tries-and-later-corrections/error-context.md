# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: calculation-supplements.spec.mjs >> calculation supplements survive retries and later corrections
- Location: tests/ui/calculation-supplements.spec.mjs:8:3

# Error details

```
TypeError: Cannot read properties of undefined (reading 'calculation_followup')
```

# Page snapshot

```yaml
- generic [ref=f1e1]:
  - link "跳到主要内容" [ref=f1e2] [cursor=pointer]:
    - /url: "#main"
  - generic [ref=f1e3]:
    - complementary "主导航" [ref=f1e4]:
      - link "澄心 CHENGXIN HEALTH" [ref=f1e5] [cursor=pointer]:
        - /url: "#consultation"
        - generic [ref=f1e6]:
          - strong [ref=f1e7]: 澄心
          - generic [ref=f1e8]: CHENGXIN HEALTH
      - button "新的问诊 ↗" [ref=f1e9] [cursor=pointer]:
        - text: 新的问诊
        - generic [ref=f1e12]: ↗
      - generic [ref=f1e13]:
        - text: 界面模式
        - combobox "界面模式" [ref=f1e14]:
          - option "患者视图" [selected]
          - option "研发视图"
      - paragraph [ref=f1e15]: 工作空间
      - navigation [ref=f1e16]:
        - button "问诊" [ref=f1e17] [cursor=pointer]
        - button "会谈记录 1" [ref=f1e21] [cursor=pointer]:
          - text: 会谈记录
          - generic [ref=f1e24]: "1"
      - generic [ref=f1e25]:
        - generic [ref=f1e26]: 最近问诊
        - generic [ref=f1e27]: "1"
      - button "·新的问诊" [ref=f1e29] [cursor=pointer]
      - generic [ref=f1e30]:
        - generic [ref=f1e33]:
          - text: 本机工作空间
          - generic [ref=f1e34]: 会谈仅保留到服务关闭
        - generic [ref=f1e35]:
          - generic [ref=f1e36]: 研究原型
          - generic [ref=f1e37]: 能力协议 v1
    - generic [ref=f1e38]:
      - banner [ref=f1e39]:
        - generic [ref=f1e40]:
          - generic [ref=f1e41]: 工作空间
          - generic [ref=f1e42]: /
          - strong [ref=f1e43]: 问诊
        - generic [ref=f1e44]:
          - generic [ref=f1e45]: 本机服务已连接
          - generic [ref=f1e47]: 直接模型
      - main [ref=f1e48]:
        - region "问诊" [ref=f1e49]:
          - generic [ref=f1e50]:
            - heading "补充信息待处理" [level=1] [ref=f1e52]
            - button "问诊记录 0" [ref=f1e53] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=f1e56]: "0"
          - generic [ref=f1e57]:
            - region "问诊对话" [ref=f1e58]:
              - generic [ref=f1e59]:
                - generic [ref=f1e62]:
                  - strong [ref=f1e63]: 澄心问诊助手
                  - generic [ref=f1e64]: 直接模型 · 本次会谈
                - generic [ref=f1e65]:
                  - generic [ref=f1e66]: 补充待处理
                  - button "紧急求助" [ref=f1e67] [cursor=pointer]
              - log "会谈消息" [ref=f1e71]:
                - generic [ref=f1e72]: 你好，这次是你自己还是替家人咨询？请先说说最困扰的问题，以及大约从什么时候开始。
                - generic [ref=f1e75]:
                  - generic [ref=f1e76]: 你
                  - generic [ref=f1e77]: 请把1.25kg换算成g；昨晚用过药物甲。
                - generic [ref=f1e78]: 核对这三个输入，再开始换算。
                - generic [ref=f1e81]:
                  - generic [ref=f1e82]: 你
                  - generic [ref=f1e83]: 核对换算：1.25 kg → g
                - article [ref=f1e85]:
                  - generic [ref=f1e86]:
                    - paragraph [ref=f1e87]: 可解释单位换算
                    - generic [ref=f1e88]: 已计算 · 输入已核对
                  - heading "1250 g" [level=3] [ref=f1e89]
                  - generic [ref=f1e90]:
                    - generic [ref=f1e91]:
                      - term [ref=f1e92]: 输入数值
                      - definition [ref=f1e93]: "1.25"
                    - generic [ref=f1e94]:
                      - term [ref=f1e95]: 原单位
                      - definition [ref=f1e96]: kg
                    - generic [ref=f1e97]:
                      - term [ref=f1e98]: 目标单位
                      - definition [ref=f1e99]: g
                  - paragraph [ref=f1e100]: 1.25 × 1000 ÷ 1 = 1250 g
                  - paragraph [ref=f1e101]: 输入已核对；实际测量质量未评价。
                  - group [ref=f1e102]:
                    - generic "执行依据与限制" [ref=f1e103]
                - generic [ref=f1e104]: 换算已完成，参数已核对。实际测量质量仍未评价。
                - generic [ref=f1e107]:
                  - generic [ref=f1e108]: 你
                  - generic [ref=f1e109]: 继续处理补充
                - generic [ref=f1e110]: 这一步的对话服务暂时不可用，已保存的内容会保留。请稍后重试，或先查看目前记录。
                - group [ref=f1e114]:
                  - generic "还有同轮补充" [ref=f1e115] [cursor=pointer]
              - generic [ref=f1e116]:
                - generic "快捷回答" [ref=f1e117]:
                  - button "重试这一轮" [ref=f1e118] [cursor=pointer]
                - generic [ref=f1e119]: 补充原话已保留，可以重试。
                - generic [ref=f1e120]:
                  - generic [ref=f1e121]: 输入你的描述
                  - textbox "输入你的描述" [active] [ref=f1e122]:
                    - /placeholder: 按自己的话说，也可以补充或更正…
                  - button "发送回答" [ref=f1e123] [cursor=pointer]
                - paragraph [ref=f1e126]:
                  - generic [ref=f1e127]: 0 / 2000
            - complementary "问诊记录" [ref=f1e128]:
              - generic [ref=f1e129]:
                - generic [ref=f1e130]: 本次问诊
                - generic [ref=f1e131]: 补充待处理
              - strong [ref=f1e133]: 先了解主要问题
              - generic [ref=f1e134]:
                - heading "已记录的信息" [level=2] [ref=f1e135]
                - generic [ref=f1e136]: 0 项
              - paragraph [ref=f1e141]: 信息将随会谈逐步记录。
              - generic [ref=f1e142]:
                - generic [ref=f1e143]: 已提供
                - generic [ref=f1e145]: 待确认 / 未知
              - paragraph [ref=f1e147]: 记录可以更正。没有提到的信息，不会自动记为“没有”。
          - generic [ref=f1e148]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  43  |   ];
  44  |   const server = createServer(async (request, response) => {
  45  |     let raw = "";
  46  |     for await (const chunk of request) raw += chunk;
  47  |     requests.push(JSON.parse(raw));
  48  |     const command = commands.shift();
  49  |     response.writeHead(command ? 200 : command === null ? 503 : 500, {
  50  |       "Content-Type": "application/json",
  51  |     });
  52  |     response.end(
  53  |       JSON.stringify(
  54  |         command
  55  |           ? {
  56  |               choices: [
  57  |                 {
  58  |                   message: {
  59  |                     role: "assistant",
  60  |                     content: JSON.stringify(command),
  61  |                   },
  62  |                   finish_reason: "stop",
  63  |                 },
  64  |               ],
  65  |             }
  66  |           : { error: "Synthetic service failure" },
  67  |       ),
  68  |     );
  69  |   });
  70  |   server.listen(0, "127.0.0.1");
  71  |   await once(server, "listening");
  72  |   return {
  73  |     endpoint: `http://127.0.0.1:${server.address().port}/v1`,
  74  |     requests,
  75  |     async close() {
  76  |       server.closeAllConnections();
  77  |       await new Promise((resolve) => server.close(resolve));
  78  |     },
  79  |   };
  80  | }
  81  | 
  82  | export async function verifyCalculationSupplements(
  83  |   page,
  84  |   model,
  85  |   { shots, mobile = false } = {},
  86  | ) {
  87  |   const errors = [];
  88  |   page.on("pageerror", (error) => errors.push(error.message));
  89  |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  90  |   const send = async (input) => {
  91  |     const response = page.waitForResponse(
  92  |       (r) =>
  93  |         r.request().method() === "POST" &&
  94  |         /\/api\/sessions\/[^/]+\/messages$/.test(r.url()),
  95  |     );
  96  |     await page.getByRole("textbox", { name: "输入你的描述" }).fill(input);
  97  |     await page.getByRole("button", { name: "发送回答" }).click();
  98  |     return (await response).json();
  99  |   };
  100 |   const snapshot = (id) =>
  101 |     page.evaluate(
  102 |       async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
  103 |       id,
  104 |     );
  105 |   const initial = await send(text),
  106 |     id = initial.state.session_id;
  107 |   const source = initial.reply.calculation_followup.source_turn_id;
  108 |   const form = page.locator("#active-question .calculation-form");
  109 |   await expect(form).toBeVisible();
  110 |   await expect(
  111 |     page.locator("#active-question .interaction-followup summary"),
  112 |   ).toHaveText("还有同轮补充");
  113 |   await form.getByRole("button", { name: "核对后计算", exact: true }).click();
  114 |   await expect(
  115 |     page.locator("#active-question .patient-calculation-result h3"),
  116 |   ).toHaveText("1250 g");
  117 |   const continueButton = page.getByRole("button", {
  118 |     name: "继续处理补充",
  119 |     exact: true,
  120 |   });
  121 |   await expect(continueButton).toBeVisible();
  122 |   const bounds = await continueButton.boundingBox(),
  123 |     area = await page.locator("#active-question").boundingBox();
  124 |   expect(bounds.y).toBeGreaterThanOrEqual(area.y);
  125 |   expect(bounds.y + bounds.height).toBeLessThanOrEqual(area.y + area.height);
  126 |   expect(model.requests).toHaveLength(1);
  127 |   await page.reload();
  128 |   await page.locator("#recent-sessions .recent-item").first().click();
  129 |   await expect(continueButton).toBeVisible();
  130 |   if (shots) await page.screenshot({ path: `${shots}-result.png` });
  131 |   await continueButton.click();
  132 |   await expect(
  133 |     page.getByRole("button", { name: "重试这一轮", exact: true }),
  134 |   ).toBeVisible();
  135 |   await expect(page.locator("#view-consultation h1")).toHaveText(
  136 |     "补充信息待处理",
  137 |   );
  138 |   await expect(page.locator("#composer-context")).toHaveText(
  139 |     "补充原话已保留，可以重试。",
  140 |   );
  141 |   const failed = await snapshot(id);
  142 |   expect(failed.state.calculations).toHaveLength(1);
> 143 |   expect(failed.reply.calculation_followup.status).toBe("pending");
      |                       ^ TypeError: Cannot read properties of undefined (reading 'calculation_followup')
  144 |   const pending = page
  145 |     .locator('#messages .interaction-followup[data-status="pending"]')
  146 |     .last();
  147 |   await pending.locator("summary").click();
  148 |   await expect(pending.locator("blockquote")).toHaveText(text);
  149 |   if (shots) await page.screenshot({ path: `${shots}-retry.png` });
  150 |   await page.getByRole("button", { name: "重试这一轮", exact: true }).click();
  151 |   await expect(
  152 |     page.locator("#active-question .interaction-followup"),
  153 |   ).toContainText("补充已带入问诊");
  154 |   const recovered = await snapshot(id);
  155 |   expect(recovered.state.calculations).toHaveLength(1);
  156 |   expect(recovered.state.facts.medications_taken.turn_id).toBe(source);
  157 |   expect(recovered.state.facts.medications_taken.evidence).toBe(
  158 |     "昨晚用过药物甲",
  159 |   );
  160 |   expect(model.requests).toHaveLength(4);
  161 |   await page.locator("#new-session").click();
  162 |   const second = await send(earlier),
  163 |     other = second.state.session_id;
  164 |   const oldAction = second.reply.action_id;
  165 |   const updated = await send("先不换算了，我34岁。");
  166 |   expect(updated.state.facts.age_years.value).toBe(34);
  167 |   const ageId = updated.state.facts.age_years.fact_id;
  168 |   const process = page.locator("#active-question .followup-process");
  169 |   await expect(process).toBeVisible();
  170 |   const stale = await page.evaluate(
  171 |     async ({ other, oldAction, id }) =>
  172 |       (
  173 |         await fetch(`/api/sessions/${other}/messages`, {
  174 |           method: "POST",
  175 |           headers: { "Content-Type": "application/json" },
  176 |           body: JSON.stringify({
  177 |             text: "处理旧页面补充",
  178 |             choice_value: `process_calculation_supplement:${id}`,
  179 |             expected_action_id: oldAction,
  180 |           }),
  181 |         })
  182 |       ).status,
  183 |     { other, oldAction, id: second.reply.calculation_followup.id },
  184 |   );
  185 |   expect(stale).toBe(409);
  186 |   expect(model.requests).toHaveLength(7);
  187 |   if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  188 |   const processBounds = await process.boundingBox();
  189 |   expect(processBounds.y + processBounds.height).toBeLessThanOrEqual(
  190 |     await page.evaluate(() => innerHeight),
  191 |   );
  192 |   expect(
  193 |     await page.evaluate(
  194 |       () => document.documentElement.scrollWidth <= innerWidth,
  195 |     ),
  196 |   ).toBe(true);
  197 |   if (shots) await page.screenshot({ path: `${shots}-pending.png` });
  198 |   await process.click();
  199 |   await expect(
  200 |     page.locator("#active-question .interaction-followup"),
  201 |   ).toContainText("补充已带入问诊");
  202 |   const final = await snapshot(other);
  203 |   expect(final.state.facts.age_years.value).toBe(34);
  204 |   expect(final.state.facts.age_years.fact_id).toBe(ageId);
  205 |   expect(final.state.facts.medications_taken.turn_id).toBe(
  206 |     second.reply.calculation_followup.source_turn_id,
  207 |   );
  208 |   expect(final.state.calculations).toEqual([]);
  209 |   expect(
  210 |     final.events.some(
  211 |       (event) =>
  212 |         event.event_type === "tool.completed" &&
  213 |         JSON.stringify(event.payload).includes("不能覆盖随后"),
  214 |     ),
  215 |   ).toBe(true);
  216 |   expect(model.requests).toHaveLength(10);
  217 |   expect(errors).toEqual([]);
  218 | }
  219 | 
```