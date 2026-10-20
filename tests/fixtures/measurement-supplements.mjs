import { expect } from "@playwright/test";
import {
  createRepeatedObservationModel,
  sendPatientText,
} from "./repeated-observations.mjs";

const medication = "昨晚用药物甲2次，具体用量不清楚";
const secondMedication = "今天用过药物乙，用量不清楚";
const report = `腋温37.6℃；更正：${medication}。`;
const secondReport = `腋温38.6℃，读数可能不准；更正：${secondMedication}。`;
const update = (value) => ({
  tool: "record_facts",
  arguments: {
    facts: [
      {
        key: "medications_taken",
        value,
        evidence: value,
        assertion: "present",
        correction: true,
      },
    ],
  },
});

export function createMeasurementSupplementsModel() {
  return createRepeatedObservationModel([
    null,
    update(medication),
    { tool: "review_summary", arguments: {} },
    update(secondMedication),
    { tool: "review_summary", arguments: {} },
  ]);
}

export async function verifyMeasurementSupplements(
  page,
  model,
  screenshotPrefix,
) {
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  const send = (text) => sendPatientText(page, text);
  const current = () => page.locator(".summary-card.current-summary");
  await send(
    "我自己32岁，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。",
  );
  await expect(page.locator("#active-task")).toBeVisible();
  await send("腋温38.25℃");
  await expect(page.locator("#active-question")).toBeVisible();
  await send(
    "总体变化不大；不适程度3分；没有其他不适；没有气促，能完整说话；干咳，没有咳血；能正常喝水吃饭，排尿正常；没有用药；过敏不知道；没有相关既往病史；想整理信息去就医。",
  );
  await expect(current().locator(".observation-value")).toHaveText("38.25 ℃");
  expect(model.requests).toHaveLength(4);
  await current()
    .getByRole("button", { name: "再测一次", exact: true })
    .click();
  await expect(page.locator("#active-task .illustrated-task")).toBeVisible();
  await send(report);
  await expect(
    page.getByRole("button", { name: "重试这一轮", exact: true }),
  ).toBeVisible();
  const pending = page.locator(".observation-supplement").last();
  await expect(page.locator("#view-consultation h1")).toHaveText(
    "补充信息待处理",
  );
  await expect(page.locator("#composer-context")).toHaveText(
    "读数已保留，可以重试补充信息。",
  );
  await expect(pending).toContainText("补充信息 · 待重试");
  await pending.locator(":scope > summary").click();
  await expect(pending.locator("blockquote")).toHaveText(report);
  const snapshot = () =>
    page.evaluate(
      async (sessionId) =>
        (await fetch(`/api/sessions/${sessionId}/snapshot`)).json(),
      id,
    );
  const failed = await snapshot();
  expect(failed.state.facts.temperature_c.value).toBe(37.6);
  const temperatureId = failed.state.facts.temperature_c.fact_id;
  if (screenshotPrefix)
    await page.screenshot({
      path: `${screenshotPrefix}-pending.png`,
      fullPage: true,
    });
  await page.getByRole("button", { name: "重试这一轮", exact: true }).click();
  await expect(
    current().locator(".summary-item[data-fact-key='medications_taken']"),
  ).toContainText(medication);
  await expect(current().locator(".observation-supplement")).toContainText(
    "已带入本轮问诊",
  );
  const repaired = await snapshot();
  expect(repaired.state.facts.temperature_c.fact_id).toBe(temperatureId);
  expect(
    repaired.state.history
      .filter((f) => f.key === "temperature_c")
      .map((f) => f.value),
  ).toEqual([38.25, 37.6]);
  expect(repaired.state.facts.temperature_c.turn_id).toBe(
    repaired.state.facts.medications_taken.turn_id,
  );
  expect(model.requests).toHaveLength(7);
  const observed = model.requests
    .slice(4)
    .map((r) =>
      JSON.parse(r.messages.find((message) => message.role === "user").content),
    );
  expect(observed.every((c) => c.latest_user_text === report)).toBe(true);
  expect(
    observed.every(
      (c) => c.completed_observation_with_supplement.text === report,
    ),
  ).toBe(true);
  await current()
    .getByRole("button", { name: "再测一次", exact: true })
    .click();
  await expect(page.locator("#active-task .illustrated-task")).toBeVisible();
  await send(secondReport);
  await expect(
    current().locator(".summary-item[data-fact-key='medications_taken']"),
  ).toContainText(secondMedication);
  await expect(current().locator(".observation-value")).toHaveText("37.6 ℃");
  await expect(current().locator(".observation-current-label")).toHaveText(
    "上次记录 · 本次未完成",
  );
  const supplement = current().locator(".observation-supplement");
  await supplement.locator(":scope > summary").click();
  await expect(supplement.locator("blockquote")).toHaveText(secondReport);
  const final = await snapshot();
  expect(final.state.observation_attempts.map((a) => a.status)).toEqual([
    "recorded",
    "unconfirmed",
  ]);
  expect(
    final.state.history.filter((f) => f.key === "temperature_c"),
  ).toHaveLength(2);
  expect(model.requests).toHaveLength(9);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  if (screenshotPrefix)
    await page.screenshot({
      path: `${screenshotPrefix}-summary.png`,
      fullPage: true,
    });
}
