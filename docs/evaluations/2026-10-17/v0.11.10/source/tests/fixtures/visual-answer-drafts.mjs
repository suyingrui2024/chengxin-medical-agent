import { expect } from "@playwright/test";

export async function degreeSnapshot(page, id) {
  return page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
    id,
  );
}

export async function reachDegree(page) {
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  const answer = async (text) => {
    await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
    await page.getByRole("button", { name: "发送回答" }).click();
    await expect(page.locator("#send-button")).toBeEnabled();
  };
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await answer("32岁");
  await answer("头疼");
  await page.getByRole("button", { name: "都没有", exact: true }).click();
  await answer("昨天逐渐出现");
  await page.getByRole("button", { name: "变化不大", exact: true }).click();
  await expect(page.locator("#active-question .severity-scale")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "先选择分数", exact: true }),
  ).toBeDisabled();
  return id;
}

export async function verifyDegreeDraft(page, { shot } = {}) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const id = await reachDegree(page);
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  const draft = "昨晚用过药物甲，这段还没有发送。";
  const before = await degreeSnapshot(page, id);
  await input.fill(draft);
  for (const degree of [0, 10, 5]) {
    const button = page.getByRole("button", {
      name: `${degree} 分`,
      exact: true,
    });
    await button.focus();
    await page.keyboard.press("Space");
    await expect(page.locator(".severity-value")).toHaveText(`${degree} 分`);
    await expect(input).toHaveValue(draft);
    await expect(
      page.getByRole("button", { name: `确认 ${degree} 分`, exact: true }),
    ).toBeEnabled();
  }
  const selected = await degreeSnapshot(page, id);
  expect(selected.state.facts).toEqual(before.state.facts);
  expect(selected.events).toEqual(before.events);
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  const geometry = await page.evaluate(() => {
    const pane = document
      .querySelector("#active-question")
      .getBoundingClientRect();
    const selectors = [
      "#active-question h2",
      ".severity-options",
      ".severity-confirm",
      "#message-input",
    ];
    return selectors.map((selector) => {
      const rect = document.querySelector(selector).getBoundingClientRect();
      return {
        selector,
        visible:
          rect.top >= 0 &&
          rect.bottom <= innerHeight &&
          rect.left >= 0 &&
          rect.right <= innerWidth &&
          (selector === "#message-input" ||
            (rect.top >= pane.top && rect.bottom <= pane.bottom)),
      };
    });
  });
  expect(geometry.filter((item) => !item.visible)).toEqual([]);
  await page.locator(".severity-confirm").evaluate((node) => {
    window.oldDegreeConfirm = node;
  });
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.url().endsWith(`/sessions/${id}/messages`),
  );
  await page.getByRole("button", { name: "确认 5 分", exact: true }).click();
  const result = await (await response).json();
  expect(result.state.facts.severity.value).toBe(5);
  expect(result.state.facts.medications_taken).toBeUndefined();
  await expect(page.locator(".severity-scale")).toHaveCount(0);
  await expect(input).toHaveValue(draft);
  const after = await degreeSnapshot(page, id);
  const added = after.events
    .slice(before.events.length)
    .filter((e) => e.event_type === "turn.user_message");
  expect(added).toHaveLength(1);
  expect(added[0].payload.text).toBe("5分");
  await page.evaluate(() => {
    window.oldDegreeConfirm.disabled = false;
    window.oldDegreeConfirm.click();
  });
  const stale = await degreeSnapshot(page, id);
  expect(stale.events).toEqual(after.events);
  expect(stale.state.facts).toEqual(after.state.facts);
  expect(errors).toEqual([]);
}
