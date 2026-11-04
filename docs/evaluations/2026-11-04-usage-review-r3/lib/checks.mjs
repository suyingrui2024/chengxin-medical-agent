/**
 * 断言库：每条都能被一行 JS 判定，结果写进 findings-r3.json。
 *
 * 本轮不改代码，所以断言不设 --gate 阻断；它的价值是让报告里的每条
 * 结论都能被下一次改动重新跑一遍，而不是只能翻截图。
 */

/** 横向溢出：排除离屏抽屉（transform 移出视口的那些不算缺陷）。 */
export async function noOverflow(page, scope = "body *") {
  const bad = await page.evaluate((selector) => {
    const out = [];
    const drawer = document.querySelector("#facts-panel");
    const drawerOpen = drawer?.classList.contains("open");
    for (const el of document.querySelectorAll(selector)) {
      if (!el.offsetParent) continue;
      // 「已记录信息」是离屏抽屉：未展开时它整块在视口右侧之外，不算溢出。
      if (!drawerOpen && el.closest("#facts-panel, #facts-scrim")) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 40) continue;
      if (rect.right <= window.innerWidth + 2) continue;
      const style = getComputedStyle(el);
      const parked =
        style.transform !== "none" && rect.left >= window.innerWidth;
      if (parked) continue;
      out.push({
        tag: el.tagName.toLowerCase(),
        cls: String(el.className).slice(0, 48),
        right: Math.round(rect.right),
        viewport: window.innerWidth,
      });
    }
    return out.slice(0, 8);
  }, scope);
  return { pass: bad.length === 0, actual: bad };
}

/** 患者可见文本里是否混进了内部枚举/null。 */
export async function noInternalTokens(page, selector) {
  const text = await page
    .locator(selector)
    .first()
    .innerText()
    .catch(() => "");
  const patterns =
    /\b(self|child|other_adult|male|female|stable|worsening|improving|null|undefined|None|unknown|closed|routine_visit|self_care|urgent_today)\b/;
  const hit = patterns.exec(text);
  return {
    pass: !hit,
    actual: hit
      ? {
          token: hit[0],
          sample: text.slice(Math.max(0, hit.index - 40), hit.index + 40),
        }
      : "clean",
  };
}

/** 可见元素计数。 */
export async function countVisible(page, selector) {
  const count = await page.locator(selector).count();
  return { pass: count > 0, actual: count };
}

export async function textOf(page, selector) {
  const text = await page
    .locator(selector)
    .first()
    .innerText()
    .catch(() => null);
  return { pass: Boolean(text), actual: text };
}

/** 关键按钮的可点尺寸（WCAG 2.5.5 取 44px 作为参照，不是硬标准）。 */
export async function minTarget(page, selector, min = 44) {
  const small = await page.evaluate(
    ([sel, size]) => {
      const out = [];
      for (const el of document.querySelectorAll(sel)) {
        if (!el.offsetParent) continue;
        const rect = el.getBoundingClientRect();
        if (rect.width < size || rect.height < size) {
          out.push({
            text: (el.textContent ?? "").trim().slice(0, 20),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
          });
        }
      }
      return out.slice(0, 8);
    },
    [selector, min],
  );
  return { pass: small.length === 0, actual: small };
}

/** 触发一次下载并读回文本（.md/.txt 的正文必须真的打开看）。 */
export async function downloadText(page, trigger) {
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 20000 }),
    trigger(),
  ]);
  const file = await download.path();
  const name = download.suggestedFilename();
  const { readFileSync } = await import("node:fs");
  const text = readFileSync(file, "utf-8");
  return { pass: text.length > 0, actual: { name, chars: text.length, text } };
}

export async function clipboardText(page) {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  const text = await page.evaluate(() => navigator.clipboard.readText());
  return { pass: Boolean(text), actual: text };
}

/** 统一入口：跑一条断言并归档。 */
export async function check(page, rec, spec) {
  let result;
  try {
    result = await spec.run(page);
  } catch (error) {
    result = { pass: false, actual: { error: String(error).slice(0, 300) } };
  }
  const pass = typeof result === "boolean" ? result : Boolean(result?.pass);
  const actual = typeof result === "boolean" ? result : result?.actual;
  return rec.assert({
    id: spec.id,
    severity: spec.severity,
    group: spec.group,
    persona: spec.persona,
    expected: spec.expected,
    actual,
    pass,
    notes: spec.note,
    evidence: spec.evidence ?? [],
  });
}

/** 会话状态断言：比看 DOM 稳，用来钉住"记录真的进了 state"。 */
export async function stateAt(page, sessionId, path) {
  const snap = await page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
    sessionId,
  );
  return path
    .split(".")
    .reduce((acc, key) => (acc == null ? acc : acc[key]), snap.state);
}
