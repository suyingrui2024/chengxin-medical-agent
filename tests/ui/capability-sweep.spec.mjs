import { test, expect } from "@playwright/test";
import fs from "node:fs";
import {
  createSweepModel,
  answerScale,
  answerFormula,
} from "../fixtures/capability-sweep.mjs";

const catalog = JSON.parse(
  fs.readFileSync(
    new URL("../fixtures/patient-capabilities.json", import.meta.url),
    "utf8",
  ),
);

// Unit conversion has its own dedicated specs; this sweep covers the
// scale and formula cards, which share one renderer but carry per-pack
// data (25 items here, 6 long options there) that a single example
// cannot stand in for.
const targets = catalog.filter((item) => item.kind !== "conversion");

test.describe.configure({ mode: "serial" });

let model;

test.beforeAll(async ({ request }) => {
  model = await createSweepModel(catalog);
  const response = await request.post("/api/settings", {
    data: {
      backend: "compatible",
      base_url: model.endpoint,
      model: "fixture",
      api_key: "sweep-only",
    },
  });
  expect(response.ok()).toBe(true);
});

test.afterAll(async ({ request }) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
  await model.close();
});

for (const target of targets) {
  test(`${target.id} renders, fits and computes`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.getByRole("button", { name: "开始问诊", exact: true }).click();

    const response = page.waitForResponse(
      (candidate) =>
        candidate.request().method() === "POST" &&
        /\/api\/sessions\/[^/]+\/messages$/.test(candidate.url()),
    );
    const alias = target.aliases[0];
    await page
      .getByRole("textbox", { name: "输入你的描述" })
      .fill(`帮我做个 ${alias}`);
    await page.getByRole("button", { name: "发送回答" }).click();
    const payload = await (await response).json();
    const sessionId = payload.state.session_id;
    const firstReply = payload.reply || payload;
    expect(
      firstReply.kind,
      `replied: ${String(firstReply.message).slice(0, 60)}`,
    ).toBe("calculation_input");

    const form = page.locator(
      "#active-question .scale-form, #active-question .formula-form",
    );
    await expect(form).toBeVisible();

    // Every declared item or input must reach the card.
    const selector = target.kind === "scale" ? "fieldset" : ".formula-field";
    await expect(form.locator(selector)).toHaveCount(target.size);

    // A phone-width card must not scroll sideways, even at 25 items.
    const width = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      viewport: innerWidth,
    }));
    expect(width.scroll).toBeLessThanOrEqual(width.viewport);

    const submitLabel = target.kind === "scale" ? "提交并计算" : "核对后计算";
    const submit = form.getByRole("button", { name: submitLabel, exact: true });

    if (target.kind === "scale") await answerScale(form);
    else await answerFormula(form, target.sample);

    await expect(submit).toBeEnabled();
    const box = await submit.boundingBox();
    expect(box.x + box.width).toBeLessThanOrEqual(390);

    await submit.click();

    const result = page.locator(
      "#active-question .scale-result, #active-question .formula-result",
    );
    await expect(result).toBeVisible();
    // A score is a record, never a clinical fact.
    const snapshot = await page.evaluate(
      async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
      sessionId,
    );
    expect(snapshot.state.calculations).toHaveLength(1);
    expect(snapshot.state.facts).toEqual({});
  });
}
