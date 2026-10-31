import { test, expect } from "@playwright/test";
import {
  createLocationAnswersModel,
  verifyLocationAnswers,
} from "../fixtures/location-answers.mjs";
import { shotPath } from "../fixtures/shots.mjs";
for (const [label, width, height, prefill] of [
  ["wide", 1440, 1080, false],
  ["mobile", 390, 844, false],
  ["short", 900, 648, false],
  ["prefill", 900, 648, true],
  ["custom", 390, 844, false],
])
  test(`location answers match region, area, side and extent on ${label}`, async ({
    page,
    request,
  }) => {
    const model = await createLocationAnswersModel(prefill);
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "local-location-answers",
            },
          })
        ).ok(),
      ).toBe(true);
      await page.setViewportSize({ width, height });
      await page.goto("/");
      await verifyLocationAnswers(page, model, {
        shotPrefix: shotPath(`77-${label}-location-answers`),
        custom: label === "custom",
      });
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
