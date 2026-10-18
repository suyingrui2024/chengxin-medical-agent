import { test, expect } from "@playwright/test";
import { reachSelfCheck } from "../fixtures/self-check-drafts.mjs";
import {
  reachDegree,
  degreeSnapshot,
} from "../fixtures/visual-answer-drafts.mjs";
import {
  expectReachable,
  verifyTemperatureReflow,
} from "../fixtures/patient-reflow.mjs";

test.beforeEach(async ({ request }) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
});

for (const [width, height] of [
  [720, 518],
  [600, 432],
  [450, 324],
  [320, 256],
]) {
  test(`temperature actions remain reachable at ${width}x${height}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    const id = await reachSelfCheck(page, "发热");
    const heading = page.locator("#active-task h3");
    await expect(heading).toBeFocused();
    const bounds = await heading.boundingBox();
    expect(bounds.y).toBeGreaterThanOrEqual(-1);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(height + 1);
    await verifyTemperatureReflow(page, id, {
      shot: () =>
        page.screenshot({ path: info.outputPath(`temperature-${width}.png`) }),
    });
  });
}

test("a real notification follows short-page flow and returns to its global host on other views", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 648 });
  await page.goto("/");
  await page.getByLabel("界面模式").selectOption("developer");
  await page
    .getByRole("button", { name: "模型与运行配置", exact: true })
    .click();
  await page.locator("#save-settings").click();
  const notice = page.locator("#toast");
  await expect(notice).toBeVisible();
  await expect(notice).toHaveText(
    "配置已应用。开始新的问诊时使用新后端，已有会谈继续保留。",
  );
  await expect(notice).toHaveCSS("position", "fixed");
  await page.setViewportSize({ width: 450, height: 324 });
  await expect(notice).toHaveCSS("position", "static");
  await expect(page.locator(".conversation-footer > #toast")).toHaveCount(1);
  await expectReachable(notice);
  await expectReachable(page.getByRole("textbox", { name: "输入你的描述" }));
  await page.setViewportSize({ width: 900, height: 648 });
  await expect(notice).toHaveCSS("position", "fixed");
  await expect(page.locator("body > #toast")).toHaveCount(1);
  await page.setViewportSize({ width: 450, height: 324 });
  await expect(notice).toHaveCSS("position", "static");
  await page.locator("#mobile-menu").click();
  await page.locator('.nav-item[data-view="history"]').click();
  await expect(page.locator("body > #toast")).toHaveCount(1);
  await expect(notice).toHaveCSS("position", "fixed");
});

test("severity choices and explicit confirmation retain their draft on a short screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 450, height: 324 });
  await page.goto("/");
  const id = await reachDegree(page),
    before = await degreeSnapshot(page, id);
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  await input.fill("这段原话还没有发送");
  for (const value of [0, 10, 3]) {
    const option = page.getByRole("button", {
      name: `${value} 分`,
      exact: true,
    });
    await expectReachable(option);
    await option.click();
    await expectReachable(
      page.getByRole("button", { name: `确认 ${value} 分`, exact: true }),
    );
  }
  await expect(input).toHaveValue("这段原话还没有发送");
  expect(await degreeSnapshot(page, id)).toEqual(before);
  await page.getByRole("button", { name: "确认 3 分", exact: true }).click();
  await expect(page.locator("#active-question h2")).toBeFocused();
  const bounds = await page.locator("#active-question h2").boundingBox();
  expect(bounds.y).toBeGreaterThanOrEqual(-1);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(325);
});

for (const [width, height] of [
  [450, 324],
  [320, 256],
]) {
  test(`body-location selection is reachable without implicit submission at ${width}x${height}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    const id = await reachSelfCheck(page),
      before = await degreeSnapshot(page, id);
    const chest = page.getByRole("button", {
      name: "在图上选择胸部",
      exact: true,
    });
    await expectReachable(chest);
    await chest.click();
    const confirm = page.getByRole("button", {
      name: "记录：胸部",
      exact: true,
    });
    await expectReachable(confirm);
    await expect(confirm).toBeEnabled();
    expect(await degreeSnapshot(page, id)).toEqual(before);
    await confirm.click();
    await expect
      .poll(
        async () =>
          (await degreeSnapshot(page, id)).state.facts.symptom_location?.value,
      )
      .toBe("胸部");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  });
}
