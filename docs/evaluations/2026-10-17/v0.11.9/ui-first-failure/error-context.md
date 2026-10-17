# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: consultation-subjects.spec.mjs >> consultation subjects stay separate and new-case text remains editable on a narrow screen
- Location: tests/ui/consultation-subjects.spec.mjs:8:3

# Error details

```
Error: locator.click: Error: strict mode violation: locator('#recent-sessions .recent-item').filter({ hasText: '头疼' }) resolved to 2 elements:
    1) <button title="头疼" type="button" class="recent-item ">头疼</button> aka getByRole('button', { name: '·头疼' }).first()
    2) <button title="头疼" type="button" class="recent-item ">头疼</button> aka getByRole('button', { name: '·头疼' }).nth(1)

Call log:
  - waiting for locator('#recent-sessions .recent-item').filter({ hasText: '头疼' })

```

# Page snapshot

```yaml
- generic [active] [ref=f1e1]:
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
        - button "会谈记录 4" [ref=f1e21] [cursor=pointer]:
          - text: 会谈记录
          - generic [ref=f1e24]: "4"
      - generic [ref=f1e25]:
        - generic [ref=f1e26]: 最近问诊
        - generic [ref=f1e27]: "4"
      - generic [ref=f1e28]:
        - button "·肚子痛" [ref=f1e29] [cursor=pointer]
        - button "·头疼" [ref=f1e30] [cursor=pointer]
        - button "·肚子痛" [ref=f1e31] [cursor=pointer]
        - button "·头疼" [ref=f1e32] [cursor=pointer]
      - generic [ref=f1e33]:
        - generic [ref=f1e36]:
          - text: 本机工作空间
          - generic [ref=f1e37]: 会谈仅保留到服务关闭
        - generic [ref=f1e38]:
          - generic [ref=f1e39]: 研究原型
          - generic [ref=f1e40]: 能力协议 v1
    - generic [ref=f1e41]:
      - banner [ref=f1e42]:
        - generic [ref=f1e43]:
          - generic [ref=f1e44]: 工作空间
          - generic [ref=f1e45]: /
          - strong [ref=f1e46]: 问诊
        - generic [ref=f1e47]:
          - generic [ref=f1e48]: 本机服务已连接
          - generic [ref=f1e50]: 直接模型
      - main [ref=f1e51]:
        - region "问诊" [ref=f1e52]:
          - generic [ref=f1e53]:
            - generic [ref=f1e54]:
              - paragraph [ref=f1e55]: A LITTLE CLARITY, ONE STEP AT A TIME
              - heading "先说说，哪里不舒服？" [level=1] [ref=f1e56]
              - paragraph [ref=f1e57]: 从你的描述开始，一起把情况理清楚。
            - button "问诊记录 0" [ref=f1e58] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=f1e61]: "0"
          - generic [ref=f1e62]:
            - region "问诊对话" [ref=f1e63]:
              - generic [ref=f1e64]:
                - generic [ref=f1e67]:
                  - strong [ref=f1e68]: 澄心问诊助手
                  - generic [ref=f1e69]: 倾听 · 澄清 · 核对
                - generic [ref=f1e70]: 准备开始
              - generic [ref=f1e73]:
                - generic [aria-hidden] [ref=f1e74]:
                  - generic [ref=f1e77]: 一步一步
                  - generic [ref=f1e78]: 清楚一点
                - heading "不用一次说完整。" [level=2] [ref=f1e81]
                - paragraph [ref=f1e82]: 你可以按自己的话描述。我们会逐步追问，必要时用图示帮助你记录和观察。
                - button "开始问诊" [ref=f1e83] [cursor=pointer]
                - generic [ref=f1e86]: 也可以从一个问题开始
                - generic [ref=f1e88]:
                  - button "01 最近有些不舒服 描述症状和变化 ↗" [ref=f1e89] [cursor=pointer]:
                    - generic [ref=f1e90]: "01"
                    - strong [ref=f1e91]: 最近有些不舒服
                    - generic [ref=f1e92]:
                      - text: 描述症状和变化
                      - generic [ref=f1e93]: ↗
                  - button "02 替家人问一问 一起梳理现有信息 ↗" [ref=f1e94] [cursor=pointer]:
                    - generic [ref=f1e95]: "02"
                    - strong [ref=f1e96]: 替家人问一问
                    - generic [ref=f1e97]:
                      - text: 一起梳理现有信息
                      - generic [ref=f1e98]: ↗
              - generic [ref=f1e99]:
                - generic "快捷回答"
                - generic [ref=f1e100]: 开始后，你可以随时补充或更正信息。
                - generic [ref=f1e101]:
                  - generic [ref=f1e102]: 输入你的描述
                  - textbox "输入你的描述" [ref=f1e103]:
                    - /placeholder: 按自己的话描述，不用担心遗漏…
                  - button "发送回答" [ref=f1e104] [cursor=pointer]
                - paragraph [ref=f1e107]:
                  - generic [ref=f1e108]: 0 / 2000
            - complementary "问诊记录" [ref=f1e109]:
              - generic [ref=f1e110]:
                - generic [ref=f1e111]: 本次问诊
                - generic [ref=f1e112]: 尚未开始
              - strong [ref=f1e114]: 先了解最困扰你的问题
              - generic [ref=f1e115]:
                - heading "已记录的信息" [level=2] [ref=f1e116]
                - generic [ref=f1e117]: 0 项
              - paragraph [ref=f1e122]: 每一步的信息，会在这里逐渐变得清晰。
              - generic [ref=f1e123]:
                - generic [ref=f1e124]: 已提供
                - generic [ref=f1e126]: 待确认 / 未知
              - paragraph [ref=f1e128]: 记录可以更正。没有提到的信息，不会自动记为“没有”。
          - generic [ref=f1e129]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  89  |   page.on("pageerror", (error) => errors.push(error.message));
  90  |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  91  |   const send = async (text) => {
  92  |     const pending = page.waitForResponse(
  93  |       (r) =>
  94  |         r.request().method() === "POST" &&
  95  |         /\/api\/sessions\/[^/]+\/messages$/.test(r.url()),
  96  |     );
  97  |     await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  98  |     await page.getByRole("button", { name: "发送回答" }).click();
  99  |     return (await pending).json();
  100 |   };
  101 |   const snapshot = (id) =>
  102 |     page.evaluate(
  103 |       async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
  104 |       id,
  105 |     );
  106 |   const first = await send(firstText),
  107 |     fatherId = first.state.session_id;
  108 |   const facts = first.state.facts;
  109 |   await expect(page.locator("#conversation-subtitle")).toHaveText(
  110 |     "父亲 · 家属代问",
  111 |   );
  112 |   const question = await page.locator("#active-question h2").textContent();
  113 |   const changed = await send(nextText);
  114 |   const card = page.locator("#active-question .subject-change-card");
  115 |   await expect(card).toBeVisible();
  116 |   await expect(card.locator(".subject-current strong")).toHaveText("父亲");
  117 |   await expect(card.locator(".subject-requested strong")).toHaveText("母亲");
  118 |   expect(changed.state.facts).toEqual(facts);
  119 |   expect(model.requests).toHaveLength(2);
  120 |   if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  121 |   const newCase = card.getByRole("button", {
  122 |     name: "为母亲新建问诊",
  123 |     exact: true,
  124 |   });
  125 |   const resume = card.getByRole("button", {
  126 |     name: "继续父亲问诊",
  127 |     exact: true,
  128 |   });
  129 |   const area = await page.locator("#active-question").boundingBox();
  130 |   for (const button of [newCase, resume]) {
  131 |     const box = await button.boundingBox();
  132 |     expect(box.y).toBeGreaterThanOrEqual(area.y);
  133 |     expect(box.y + box.height).toBeLessThanOrEqual(area.y + area.height);
  134 |   }
  135 |   expect(
  136 |     await page.evaluate(
  137 |       () => document.documentElement.scrollWidth <= innerWidth,
  138 |     ),
  139 |   ).toBe(true);
  140 |   if (shots) await page.screenshot({ path: `${shots}-choice.png` });
  141 |   await resume.click();
  142 |   await expect(page.locator("#active-question h2")).toHaveText(question);
  143 |   expect(model.requests).toHaveLength(2);
  144 |   await send(nextText);
  145 |   const stale = await page.evaluate(
  146 |     async ({ id, action }) =>
  147 |       (
  148 |         await fetch(`/api/sessions/${id}/messages`, {
  149 |           method: "POST",
  150 |           headers: { "Content-Type": "application/json" },
  151 |           body: JSON.stringify({
  152 |             text: "旧入口",
  153 |             choice_value: "resume_current_subject",
  154 |             expected_action_id: action,
  155 |           }),
  156 |         })
  157 |       ).status,
  158 |     { id: fatherId, action: changed.reply.action_id },
  159 |   );
  160 |   expect(stale).toBe(409);
  161 |   const created = page.waitForResponse(
  162 |     (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  163 |   );
  164 |   await newCase.click();
  165 |   const mother = await (await created).json(),
  166 |     motherId = mother.state.session_id;
  167 |   expect(motherId).not.toBe(fatherId);
  168 |   expect(mother.state.facts).toEqual({});
  169 |   await expect(page.getByRole("textbox", { name: "输入你的描述" })).toHaveValue(
  170 |     nextText,
  171 |   );
  172 |   expect(model.requests).toHaveLength(2);
  173 |   if (shots) await page.screenshot({ path: `${shots}-draft.png` });
  174 |   await page.getByRole("button", { name: "发送回答" }).click();
  175 |   await expect(page.locator("#conversation-subtitle")).toHaveText(
  176 |     "母亲 · 家属代问",
  177 |   );
  178 |   expect(model.requests).toHaveLength(4);
  179 |   const father = await snapshot(fatherId),
  180 |     motherState = await snapshot(motherId);
  181 |   expect(father.state.facts).toEqual(facts);
  182 |   expect(motherState.state.facts.age_years.value).toBe(65);
  183 |   expect(motherState.state.consultation_subject.label).toBe("母亲");
  184 |   if (mobile) await page.setViewportSize({ width: 1440, height: 1080 });
  185 |   await page.reload();
  186 |   await page
  187 |     .locator("#recent-sessions .recent-item")
  188 |     .filter({ hasText: "头疼" })
> 189 |     .click();
      |      ^ Error: locator.click: Error: strict mode violation: locator('#recent-sessions .recent-item').filter({ hasText: '头疼' }) resolved to 2 elements:
  190 |   await expect(card).toBeVisible();
  191 |   await expect(page.locator("#conversation-subtitle")).toHaveText(
  192 |     "父亲 · 家属代问",
  193 |   );
  194 |   await resume.click();
  195 |   await expect(page.locator("#active-question h2")).toHaveText(question);
  196 |   expect(model.requests).toHaveLength(4);
  197 |   expect(errors).toEqual([]);
  198 | }
  199 | 
```