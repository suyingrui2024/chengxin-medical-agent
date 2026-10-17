# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: delivery.spec.mjs >> native delivery checks show failure, repair within budget and restore the real check history
- Location: tests/desktop/delivery.spec.mjs:13:1

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('#execution-check-summary .execution-check-chip')
Expected substring: "退出 0"
Received string:    "!新增测试版本未核对"
Timeout: 5000ms

Call log:
  - Expect "toContainText" locator('#execution-check-summary .execution-check-chip') with timeout 5000ms
  - waiting for locator('#execution-check-summary .execution-check-chip')
    14 × locator resolved to <button type="button" class="execution-check-chip check-unresolved" title="本轮必需检查最近一次运行的状态。检查范围：整个工作区。文件变化后需复验；退出0不代表需求覆盖充分。" data-check-key="fb8c8aef0c6e7ef474d88fdad30b85a2bacce56dba17216e767e8dee9981f356">…</button>
       - unexpected value "!新增测试版本未核对"

```

```yaml
- button "! 新增测试 版本未核对"
```

# Test source

```ts
  107 |       env: environment,
  108 |     });
  109 |   let desktop;
  110 |   try {
  111 |     desktop = await launch(env);
  112 |     let page = await desktop.firstWindow();
  113 |     await expect(page.getByText("本机服务已连接")).toBeVisible();
  114 |     const settings = await page.evaluate(async () =>
  115 |       (await fetch("/api/settings")).json(),
  116 |     );
  117 |     expect(settings.base_url).toBe(endpoint);
  118 |     await page.getByLabel("界面模式").selectOption("developer");
  119 |     await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  120 |     await page
  121 |       .getByLabel("描述执行任务")
  122 |       .fill("创建并实际检查错误预期，按工具结果修正并复验。");
  123 |     await page.getByRole("button", { name: "执行任务", exact: true }).click();
  124 |     await expect.poll(() => requests.length).toBe(4);
  125 |     await expect(page.locator("#execution-model")).toContainText("4次模型调用");
  126 |     await expect(page.locator("#execution-status")).toHaveText("正在执行");
  127 |     await expect(page.locator("#execution-results")).toContainText(
  128 |       "正在修正交付检查",
  129 |     );
  130 |     await expect(page.locator(".execution-final")).toHaveCount(0);
  131 |     let chip = page.locator("#execution-check-summary .execution-check-chip");
  132 |     await expect(chip).toHaveCount(1);
  133 |     await expect(chip).toContainText("退出 1");
  134 |     await chip.focus();
  135 |     for (let index = 0; index < 2; index++) {
  136 |       const response = await page.waitForResponse((response) =>
  137 |         /\/api\/execution\/[^/?]+\?after=/.test(response.url()),
  138 |       );
  139 |       await response.finished();
  140 |       await page.evaluate(
  141 |         () =>
  142 |           new Promise((resolve) =>
  143 |             requestAnimationFrame(() => requestAnimationFrame(resolve)),
  144 |           ),
  145 |       );
  146 |       await expect(chip).toBeFocused();
  147 |     }
  148 |     await page.screenshot({
  149 |       path: "docs/assets/ui/43-delivery-check-repair.png",
  150 |       animations: "disabled",
  151 |     });
  152 |     release();
  153 |     await expect(page.locator("#execution-status")).toHaveText("本轮已完成");
  154 |     await expect(chip).toContainText("退出 0");
  155 |     await expect(chip).toHaveClass(/check-succeeded/);
  156 |     await expect(page.locator("#execution-outcome")).toContainText(
  157 |       "检查已复验",
  158 |     );
  159 |     await expect(page.locator(".execution-final")).toHaveCount(1);
  160 |     expect(requests).toHaveLength(6);
  161 |     expect(JSON.stringify(requests[2])).toContain("AssertionError");
  162 |     expect(JSON.stringify(requests[3])).toContain("交付检查纠错");
  163 |     await chip.click();
  164 |     await expect(
  165 |       page.locator(".execution-delivery details[open]").last(),
  166 |     ).toContainText("新增测试");
  167 |     const identity = path.basename(
  168 |       await page.locator("#execution-directory").textContent(),
  169 |     );
  170 |     await page
  171 |       .locator("#execution-files .execution-file")
  172 |       .filter({ hasText: "check.py" })
  173 |       .click();
  174 |     await expect(page.locator("#execution-preview")).toContainText(
  175 |       "assert 2 + 2 == 4",
  176 |     );
  177 |     await page
  178 |       .locator(".execution-delivery details[open]")
  179 |       .last()
  180 |       .locator("summary")
  181 |       .click();
  182 |     await page.locator("#execution-results").evaluate((node) => {
  183 |       node.scrollTop = node.scrollHeight;
  184 |     });
  185 |     await page.screenshot({
  186 |       path: "docs/assets/ui/44-delivery-check-result.png",
  187 |       animations: "disabled",
  188 |     });
  189 |     await expect(page.locator("#execution-close")).toBeEnabled();
  190 |     await page.locator("#execution-close").click();
  191 |     await expect(page.locator("#execution-check-summary")).toBeHidden();
  192 |     await desktop.close();
  193 |     desktop = null;
  194 |     const offline = { ...env, MEDAGENT_DESKTOP_LIVE: "0" };
  195 |     for (const key of [
  196 |       "MEDAGENT_LLM_BASE_URL",
  197 |       "MEDAGENT_LLM_API_KEY",
  198 |       "MEDAGENT_LLM_MODEL",
  199 |     ])
  200 |       delete offline[key];
  201 |     desktop = await launch(offline);
  202 |     page = await desktop.firstWindow();
  203 |     await page.getByLabel("界面模式").selectOption("developer");
  204 |     await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  205 |     await page.getByLabel("选择执行工作区").selectOption(identity);
  206 |     chip = page.locator("#execution-check-summary .execution-check-chip");
> 207 |     await expect(chip).toContainText("退出 0");
      |                        ^ Error: expect(locator).toContainText(expected) failed
  208 |     await chip.click();
  209 |     await expect(
  210 |       page.locator(".execution-delivery details[open]").last(),
  211 |     ).toContainText("已保存的运行观察");
  212 |     await expect(page.locator(".execution-delivery")).toHaveCount(2);
  213 |     expect(requests).toHaveLength(6);
  214 |   } finally {
  215 |     release();
  216 |     await desktop?.close();
  217 |     await new Promise((resolve) => server.close(resolve));
  218 |     await rm(temporary, { recursive: true, force: true });
  219 |   }
  220 | });
  221 | 
```