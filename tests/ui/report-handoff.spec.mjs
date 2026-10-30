import { expect, test } from "@playwright/test";

test("pasted report rows require patient confirmation before appearing in the handoff", async ({
  page,
  request,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const sessionId = (await (await created).json()).session_id;
  await page.getByRole("button", { name: "我自己", exact: true }).click();

  await page.getByRole("button", { name: "添加报告内容", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "核对报告内容" });
  await dialog
    .getByLabel("报告原文")
    .fill("报告日期 2026-10-20\n空腹血糖 4.2 mmol/L 3.9-6.1");
  const drafted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().includes(`/api/sessions/${sessionId}/report-drafts`),
  );
  await dialog.getByRole("button", { name: "提取待核对项目" }).click();
  expect((await drafted).ok()).toBe(true);
  await expect(dialog.getByText("空腹血糖", { exact: true })).toBeVisible();

  const before = await (
    await request.get(`/api/sessions/${sessionId}/handoff`)
  ).json();
  expect(before.handoff.report_items).toEqual([]);
  const confirmed = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().includes(`/api/sessions/${sessionId}/report-drafts/`) &&
      response.url().endsWith("/confirm"),
  );
  await dialog.getByRole("button", { name: "确认加入", exact: true }).click();
  expect((await confirmed).ok()).toBe(true);
  await expect(page.locator("#report-items-summary")).toContainText("空腹血糖");

  await dialog.getByRole("button", { name: "关闭报告录入" }).click();
  await page.getByRole("button", { name: "就诊交接摘要", exact: true }).click();
  const handoff = page.getByRole("dialog", { name: "就诊交接摘要" });
  await expect(handoff).toContainText("空腹血糖");
  await expect(handoff).toContainText("4.2 · mmol/L");
  await handoff.getByRole("button", { name: "关闭交接摘要" }).click();

  page.once("dialog", (dialogEvent) => dialogEvent.accept());
  // The report summary lives in the facts drawer, which starts closed.
  if (
    !(await page
      .locator("#facts-panel")
      .evaluate((n) => n.classList.contains("open")))
  )
    await page.locator("#toggle-facts").click();
  await page
    .locator("#report-items-summary")
    .getByRole("button", { name: "移除" })
    .click();
  await expect(page.locator("#report-items-summary")).toBeHidden();
  const after = await (
    await request.get(`/api/sessions/${sessionId}/handoff`)
  ).json();
  expect(after.handoff.report_items).toEqual([]);
  expect(errors).toEqual([]);
});
