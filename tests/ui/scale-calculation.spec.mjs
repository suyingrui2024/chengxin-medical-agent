import { test, expect } from "@playwright/test";
import { createPatientCalculationModel } from "../fixtures/patient-calculation.mjs";

// No disease word in the request: a message that also carries clinical
// content retains a same-turn supplement, and the engine then refuses to
// compute until that supplement is processed. That is correct product
// behaviour, but it is not what this spec is about.
const REQUEST = "帮我做个 GDS-15 量表";

async function startScaleForm(page) {
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const response = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "POST" &&
      /\/api\/sessions\/[^/]+\/messages$/.test(candidate.url()),
  );
  await page.getByRole("textbox", { name: "输入你的描述" }).fill(REQUEST);
  await page.getByRole("button", { name: "发送回答" }).click();
  const payload = await (await response).json();
  const form = page.locator("#active-question .scale-form");
  await expect(form).toBeVisible();
  return { form, payload };
}

test("scale form renders every item, blocks incomplete submits and scores the answers", async ({
  page,
  request,
}) => {
  const model = await createPatientCalculationModel({
    capabilityId: "calculate.gds15",
  });
  try {
    expect(
      (
        await request.post("/api/settings", {
          data: {
            backend: "compatible",
            base_url: model.endpoint,
            model: "fixture",
            api_key: "scale-test-only",
          },
        })
      ).ok(),
    ).toBe(true);
    await page.goto("/");

    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));

    const { form, payload } = await startScaleForm(page);
    const sessionId = payload.state.session_id;

    // Every item is its own fieldset with a radio group, and nothing is
    // pre-selected: the model may propose the scale, never the answers.
    await expect(form.locator("fieldset")).toHaveCount(15);
    await expect(form.locator("input[type=radio]:checked")).toHaveCount(0);
    await expect(form.locator(".scale-progress")).toHaveText("已选 0 / 15 项");
    expect(payload.state.calculations).toEqual([]);
    expect(model.requests).toHaveLength(1);

    // An incomplete submit must be reported in the card, not scored.
    for (const name of ["q1", "q2", "q3"]) {
      await form.locator(`input[name="${name}"][value="0"]`).check();
    }
    await expect(form.locator(".scale-progress")).toHaveText("已选 3 / 15 项");
    await form.getByRole("button", { name: "提交并计算", exact: true }).click();
    await expect(form.locator(".calculation-feedback")).toContainText(
      "还有 12 项没有选择",
    );

    // Answer the rest with "否"; the five reverse-scored items then count 1.
    for (let index = 4; index <= 15; index += 1) {
      await form.locator(`input[name="q${index}"][value="0"]`).check();
    }
    await expect(form.locator(".scale-progress")).toHaveText("已选 15 / 15 项");
    await form.getByRole("button", { name: "提交并计算", exact: true }).click();

    const result = page.locator("#active-question .scale-result");
    await expect(result).toBeVisible();
    await expect(result.locator(".scale-score-value")).toHaveText("5");
    await expect(result.locator(".scale-score-unit")).toContainText("15");
    // GDS-15 declares no bands: a chip would be a verdict we do not have.
    await expect(result.locator(".scale-band")).toHaveCount(0);
    await expect(result.locator(".scale-disclaimer")).toContainText(
      "不构成诊断",
    );

    const snapshot = await page.evaluate(
      async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
      sessionId,
    );
    expect(snapshot.state.calculations).toHaveLength(1);
    expect(snapshot.state.calculations[0].data.value).toBe("5");
    // A score is not a clinical fact.
    expect(snapshot.state.facts).toEqual({});
    expect(errors).toEqual([]);
    // Confirmation must not call the model again.
    expect(model.requests).toHaveLength(1);
  } finally {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await model.close();
  }
});

test("scale form stays readable on a phone and at 200% text zoom", async ({
  page,
  request,
}) => {
  const model = await createPatientCalculationModel({
    capabilityId: "calculate.gds15",
  });
  try {
    expect(
      (
        await request.post("/api/settings", {
          data: {
            backend: "compatible",
            base_url: model.endpoint,
            model: "fixture",
            api_key: "scale-test-only",
          },
        })
      ).ok(),
    ).toBe(true);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const { form } = await startScaleForm(page);

    // Options stay tappable on a narrow screen.
    const option = form.locator(".scale-option").first();
    const box = await option.boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);

    const width = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      viewport: innerWidth,
    }));
    expect(width.scroll).toBeLessThanOrEqual(width.viewport);

    // Doubling the root font size must not introduce horizontal scrolling.
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "32px";
    });
    const zoomed = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      viewport: innerWidth,
    }));
    expect(zoomed.scroll).toBeLessThanOrEqual(zoomed.viewport);

    // The submit control is reachable without horizontal panning.
    const submit = await form
      .getByRole("button", { name: "提交并计算", exact: true })
      .boundingBox();
    expect(submit.x + submit.width).toBeLessThanOrEqual(390);
  } finally {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await model.close();
  }
});
