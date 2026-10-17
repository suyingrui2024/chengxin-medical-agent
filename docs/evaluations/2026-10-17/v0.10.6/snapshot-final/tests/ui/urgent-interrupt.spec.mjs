import { test, expect } from "@playwright/test";

for (const late of ["response", "error"]) {
  test(`priority help stays available during a pending message and ignores its late ${late}`, async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "开始问诊", exact: true }).click();
    let release;
    let held = false;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    await page.route("**/api/sessions/*/messages", async (route) => {
      const payload = route.request().postDataJSON();
      if (payload.choice_value === "request_emergency_help") {
        await route.continue();
        return;
      }
      const response = await route.fetch();
      held = true;
      await gate;
      if (late === "error") await route.abort("failed");
      else await route.fulfill({ response });
    });
    try {
      await page.getByRole("button", { name: "我自己", exact: true }).click();
      await expect.poll(() => held).toBe(true);
      await expect(page.locator("#send-button")).toBeDisabled();
      await expect(page.locator("#message-input")).toBeDisabled();
      const help = page.getByRole("button", { name: "紧急求助", exact: true });
      await expect(help).toBeEnabled();
      await help.click();
      const dialog = page.getByRole("dialog", { name: "需要优先求助？" });
      await expect(dialog).toBeVisible();
      const text = '<img src=x onerror="alert(1)"> 我需要先求助';
      await page.getByLabel("现在的情况（选填）").fill(text);
      await page.screenshot({
        path: "docs/assets/ui/38-priority-help-dialog.png",
        fullPage: true,
      });
      await page.getByRole("button", { name: "优先求助", exact: true }).click();
      await expect(dialog).toBeHidden();
      await expect(page.locator("#conversation-status")).toHaveText("立即求助");
      await expect(page.locator("#messages .emergency")).toHaveCount(1);
      await expect(page.locator("#busy-indicator")).toBeHidden();
      expect(await page.locator('img[src="x"]').count()).toBe(0);
      await page.screenshot({
        path: "docs/assets/ui/39-priority-help-response.png",
        fullPage: true,
      });
      if (late === "error") {
        await page.locator("#new-session").click();
        await expect(page.locator("#conversation-status")).toHaveText(
          "明确问题",
        );
      }
      const completed = page.waitForEvent(
        late === "error" ? "requestfailed" : "requestfinished",
        (request) =>
          request.url().endsWith("/messages") &&
          request.postDataJSON()?.choice_value !== "request_emergency_help",
      );
      release();
      await completed;
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve)),
          ),
      );
      await expect(page.locator("#messages .emergency")).toHaveCount(
        late === "error" ? 0 : 1,
      );
      await expect(page.locator("#conversation-status")).toHaveText(
        late === "error" ? "明确问题" : "立即求助",
      );
      await expect(page.locator(".transport-error")).toHaveCount(0);
      await expect(page.locator("#new-session")).toBeEnabled();
    } finally {
      release();
      await page.unrouteAll({ behavior: "wait" });
    }
  });
}

test("priority help preserves a failed request draft and works at a narrow viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "紧急求助", exact: true }).click();
  await page.getByLabel("现在的情况（选填）").fill("需要身边的人协助");
  await page.route(
    "**/api/sessions/*/messages",
    (route) => route.abort("failed"),
    { times: 1 },
  );
  await page.getByRole("button", { name: "优先求助", exact: true }).click();
  await expect(page.locator("#urgent-help-error")).toBeVisible();
  await expect(page.getByLabel("现在的情况（选填）")).toHaveValue(
    "需要身边的人协助",
  );
  await expect(
    page.getByRole("button", { name: "优先求助", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "优先求助", exact: true }).click();
  await expect(page.locator("#conversation-status")).toHaveText("立即求助");
  await expect(page.locator("#urgent-help-dialog")).toBeHidden();
  const action = page.locator(".notice-action");
  await expect(action).toBeVisible();
  await expect(action).toContainText("120");
  const bounds = await action.boundingBox();
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
  await page.screenshot({
    path: "docs/assets/ui/42-mobile-priority-response.png",
    animations: "disabled",
  });
});
