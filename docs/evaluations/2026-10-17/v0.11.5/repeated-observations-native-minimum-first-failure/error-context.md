# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: repeated-observations.spec.mjs >> native repeat resumes the original question with all options visible at 900x680
- Location: tests/desktop/repeated-observations.spec.mjs:16:3

# Error details

```
Error: expect(locator).toBeInViewport() failed

Locator:  locator('#active-question').getByRole('button').first()
Expected: in viewport
Received: viewport ratio 0.5300925970077515
Timeout:  5000ms

Call log:
  - Expect "toBeInViewport" locator('#active-question').getByRole('button').first() with timeout 5000ms
  - waiting for locator('#active-question').getByRole('button').first()
    14 × locator resolved to <button type="button" aria-label="在加重" class="visual-choice choice-worsening">…</button>
       - unexpected value "viewport ratio 0.5300925970077515"

```

```yaml
- button "在加重":
  - strong: 在加重
  - text: ↗
```

# Test source

```ts
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
  205 | export async function verifyResumedObservationQuestion(
  206 |   page,
  207 |   model,
  208 |   screenshot,
  209 | ) {
  210 |   await expect(page.getByText("本机服务已连接")).toBeVisible();
  211 |   await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  212 |   const send = async (text) => {
  213 |     await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  214 |     await page.getByRole("button", { name: "发送回答" }).click();
  215 |   };
  216 |   await send(
  217 |     "我自己32岁，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。",
  218 |   );
  219 |   await expect(page.locator("#active-task")).toBeVisible();
  220 |   await send("腋温38.25℃");
  221 |   await expect(page.locator("#active-question")).toBeVisible();
  222 |   const question = await page.locator("#active-question").innerText();
  223 |   await send("想重新测温");
  224 |   await expect(
  225 |     page.locator("#active-task .observation-previous"),
  226 |   ).toContainText("38.25 ℃");
  227 |   await send("腋温37.6℃");
  228 |   await expect(page.locator("#active-question")).toHaveText(question, {
  229 |     useInnerText: true,
  230 |   });
  231 |   await expect(
  232 |     page.locator("#active-observation .observation-value"),
  233 |   ).toHaveText("37.6 ℃");
  234 |   await page
  235 |     .locator("#active-observation")
  236 |     .getByRole("button", { name: "再测一次", exact: true })
  237 |     .click();
  238 |   await page.getByRole("button", { name: "暂时跳过", exact: true }).click();
  239 |   await expect(page.locator("#active-question")).toHaveText(question, {
  240 |     useInnerText: true,
  241 |   });
  242 |   await expect(page.locator("#active-observation")).toContainText(
  243 |     "上次记录 · 本次未完成",
  244 |   );
  245 |   expect(model.requests).toHaveLength(2);
  246 |   expect(
  247 |     await page.evaluate(
  248 |       () => document.documentElement.scrollWidth <= innerWidth,
  249 |     ),
  250 |   ).toBe(true);
  251 |   await expect(
  252 |     page.getByRole("textbox", { name: "输入你的描述" }),
  253 |   ).toBeInViewport();
  254 |   const choices = page.locator("#active-question").getByRole("button");
  255 |   await expect(choices).toHaveCount(4);
  256 |   for (const choice of await choices.all())
> 257 |     await expect(choice).toBeInViewport({ ratio: 1 });
      |                          ^ Error: expect(locator).toBeInViewport() failed
  258 |   if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });
  259 | }
  260 | 
```