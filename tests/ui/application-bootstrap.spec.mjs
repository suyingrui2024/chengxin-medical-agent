import { test, expect } from "@playwright/test";

test("reconnecting and booting again bind each patient action only once", async ({
  page,
}) => {
  const errors = [];
  const creations = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/api/sessions")
    ) {
      creations.push(request.url());
    }
  });
  await page.goto("/");
  await expect(page.locator("#workspace-mode")).toBeEnabled();
  await page.evaluate(async () => {
    const { boot, init } = await import("/js/app/bootstrap.js");
    await init();
    await boot();
  });
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/sessions"),
  );
  await page.locator("#start-consultation").click();
  await created;
  await expect(page.locator("#message-input")).toBeEnabled();
  expect(creations).toHaveLength(1);
  expect(errors).toEqual([]);
});

test("switching sessions clears export context and deleting clears refresh recovery", async ({
  page,
}) => {
  await page.goto("/");
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/sessions"),
  );
  await page.locator("#start-consultation").click();
  const first = await (await created).json();
  const input = page.locator("#message-input");
  await expect(input).toBeEnabled();
  await input.fill("这段草稿还未发送");
  await page.locator("#open-handoff").click();
  await expect(page.locator("#handoff-dialog")).toBeVisible();
  expect(
    await page.evaluate(async () => {
      const { handoffUI } = await import("/js/app/context.js");
      const writes = [];
      navigator.clipboard.writeText = async (text) => writes.push(text);
      await handoffUI.copy();
      return writes.length === 1;
    }),
  ).toBe(true);
  await page.locator("#handoff-dialog .close-dialog").first().click();
  await page.locator("#new-session").click();
  await expect(input).toBeEnabled();
  await expect(input).toHaveValue("");
  expect(
    await page.evaluate(async () => {
      const { handoffUI } = await import("/js/app/context.js");
      const writes = [];
      navigator.clipboard.writeText = async (text) => writes.push(text);
      await handoffUI.copy();
      return writes.length;
    }),
  ).toBe(0);
  // Returning to the first session keeps its unsubmitted text.
  await page
    .locator(`#recent-sessions [data-session-id="${first.session_id}"]`)
    .click();
  await expect(input).toHaveValue("这段草稿还未发送");
  await page.locator("#open-handoff").click();
  await expect(page.locator("#handoff-dialog")).toBeVisible();
  await page.locator("#handoff-dialog .close-dialog").first().click();
  await page.locator('[data-view="history"]').click();
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .locator("#history-list .history-card-row")
    .filter({
      has: page.locator(`[data-session-id="${first.session_id}"]`),
    })
    .locator(".history-delete")
    .click();
  await expect(page.locator("#welcome")).toBeVisible();
  expect(
    await page.evaluate(async () => {
      const { handoffUI } = await import("/js/app/context.js");
      const writes = [];
      navigator.clipboard.writeText = async (text) => writes.push(text);
      await handoffUI.copy();
      return {
        writes: writes.length,
        lastSession: localStorage.getItem("medagent:last-session-id"),
      };
    }),
  ).toEqual({ writes: 0, lastSession: null });
  await page.reload();
  await expect(page.locator("#welcome")).toBeVisible();
});

test("a delayed handoff belongs only to the session that requested it", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator("#start-consultation").click();
  await expect(page.locator("#message-input")).toBeEnabled();
  let release;
  const held = new Promise((resolve) => (release = resolve));
  await page.route("**/api/sessions/*/handoff", async (route) => {
    await held;
    await route.continue();
  });
  const requested = page.waitForRequest((request) =>
    request.url().endsWith("/handoff"),
  );
  const opening = page.evaluate(async () => {
    const { handoffUI } = await import("/js/app/context.js");
    await handoffUI.open();
  });
  await requested;
  await page.locator("#new-session").click();
  await expect(page.locator("#message-input")).toBeEnabled();
  release();
  await opening;
  await expect(page.locator("#handoff-dialog")).not.toBeVisible();
  expect(
    await page.evaluate(async () => {
      const { handoffUI } = await import("/js/app/context.js");
      const writes = [];
      navigator.clipboard.writeText = async (text) => writes.push(text);
      await handoffUI.copy();
      return writes;
    }),
  ).toEqual([]);
});
