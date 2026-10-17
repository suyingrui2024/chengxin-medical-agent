import { expect } from "@playwright/test";
import { createPatientCalculationModel } from "./patient-calculation.mjs";

export function createGlucoseModel() {
  return createPatientCalculationModel({
    capabilityId: "calculate.glucose",
    inputForText: (text) =>
      text.includes("180mg/dL")
        ? { value: "180", from_unit: "mg/dL", to_unit: "mmol/L" }
        : {},
  });
}

export async function verifyGlucoseCalculation(
  page,
  model,
  { shots, mobile = false, native = false, nativeHeight = 760 } = {},
) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const original = await page.locator("#active-question h2").textContent();
  const first = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      /\/api\/sessions\/[^/]+\/messages$/.test(r.url()),
  );
  await page
    .getByRole("textbox", { name: "输入你的描述" })
    .fill("请把血糖180mg/dL换成mmol/L");
  await page.getByRole("button", { name: "发送回答" }).click();
  const initial = await (await first).json();
  const form = page.locator("#active-question .calculation-form");
  await expect(form).toBeVisible();
  await expect(
    page.locator("#active-question .calculation-subject"),
  ).toHaveText("血糖（葡萄糖）单位换算");
  await expect(
    form.getByLabel("原单位", { exact: true }).locator("option"),
  ).toHaveCount(3);
  await expect(form.getByLabel("数值", { exact: true })).toHaveValue("180");
  expect(initial.state.facts).toEqual({});
  expect(initial.state.calculations).toEqual([]);
  await expect(
    page.locator("#active-question .calculation-source"),
  ).not.toHaveAttribute("open", "");
  if (shots)
    await page.screenshot({
      path: `${shots}/${native ? `65-native-${nativeHeight}` : "62"}-glucose-input.png`,
    });
  await form.getByLabel("数值", { exact: true }).fill("1e3");
  await form.getByRole("button", { name: "核对后计算", exact: true }).click();
  expect(
    await form
      .getByLabel("数值", { exact: true })
      .evaluate((el) => el.validity.valid),
  ).toBe(false);
  expect(model.requests).toHaveLength(1);
  await form.getByLabel("数值", { exact: true }).fill("10");
  await form.getByLabel("原单位", { exact: true }).selectOption("mmol/L");
  await form.getByLabel("目标单位", { exact: true }).selectOption("mg/dL");
  await form.getByRole("button", { name: "核对后计算", exact: true }).click();
  const result = page.locator("#active-question .patient-calculation-result");
  await expect(result.getByRole("heading")).toHaveText("≈ 180 mg/dL");
  await expect(result.locator(".calculation-title")).toHaveText("血糖单位换算");
  await expect(result.locator(".calculation-title")).toBeVisible();
  const continueBounds = await result
    .getByRole("button", { name: "继续问诊", exact: true })
    .boundingBox();
  const activeBounds = await page.locator("#active-question").boundingBox();
  expect(continueBounds.y).toBeGreaterThanOrEqual(activeBounds.y);
  expect(continueBounds.y + continueBounds.height).toBeLessThanOrEqual(
    activeBounds.y + activeBounds.height,
  );
  await expect(result.locator(".calculation-formula")).toHaveText(
    "10 mmol/L × 18 ≈ 180 mg/dL",
  );
  const details = result.locator("details");
  await expect(details).not.toHaveAttribute("open", "");
  if (shots)
    await page.screenshot({
      path: `${shots}/${native ? `65-native-${nativeHeight}` : "63"}-glucose-result.png`,
    });
  await result.getByText("执行依据与限制", { exact: true }).click();
  const link = result.locator(".calculation-references a");
  await expect(link).toHaveAttribute(
    "href",
    "https://diabetes.org/about-diabetes/common-terms",
  );
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await expect(result).toContainText("不判断高低");
  await expect(result).toContainText("显示位数不代表测量精度");
  const snapshot = await page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
    initial.state.session_id,
  );
  expect(snapshot.state.facts).toEqual({});
  expect(snapshot.state.calculations).toHaveLength(1);
  expect(snapshot.state.calculations[0].confirmation.original_input.value).toBe(
    "180",
  );
  expect(
    snapshot.state.calculations[0].confirmation.confirmed_input.value,
  ).toBe("10");
  expect(model.requests).toHaveLength(1);
  await result.getByText("执行依据与限制", { exact: true }).click();
  await result.getByRole("button", { name: "继续问诊", exact: true }).click();
  await expect(page.locator("#active-question h2")).toHaveText(original);
  expect(model.requests).toHaveLength(1);
  await page.locator("#new-session").click();
  await page
    .getByRole("textbox", { name: "输入你的描述" })
    .fill("请帮我做血糖单位换算，还没给数值和单位");
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(form).toBeVisible();
  for (const name of ["数值", "原单位", "目标单位"])
    await expect(form.getByLabel(name, { exact: true })).toHaveValue("");
  if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  const button = await form
    .getByRole("button", { name: "核对后计算", exact: true })
    .boundingBox();
  const bounds = await page.evaluate(() => ({
    height: innerHeight,
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(bounds.scroll).toBeLessThanOrEqual(bounds.width);
  expect(button.y + button.height).toBeLessThanOrEqual(bounds.height);
  if (shots && mobile)
    await page.screenshot({ path: `${shots}/64-mobile-glucose-input.png` });
  if (mobile) await page.setViewportSize({ width: 1440, height: 1080 });
  await page.reload();
  await page.locator("#recent-sessions .recent-item").first().click();
  await expect(form).toBeVisible();
  for (const name of ["数值", "原单位", "目标单位"])
    await expect(form.getByLabel(name, { exact: true })).toHaveValue("");
  await form.getByLabel("数值", { exact: true }).fill("97");
  await form.getByLabel("原单位", { exact: true }).selectOption("mg/dL");
  await form.getByLabel("目标单位", { exact: true }).selectOption("mmol/L");
  await form.getByRole("button", { name: "核对后计算", exact: true }).click();
  await expect(result.getByRole("heading")).toHaveText("≈ 5.3889 mmol/L");
  expect(model.requests).toHaveLength(2);
  expect(errors).toEqual([]);
}
