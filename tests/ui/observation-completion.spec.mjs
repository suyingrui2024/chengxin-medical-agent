import { test, expect } from "@playwright/test";
import {
  createObservationCompletionModel,
  verifyObservationCompletion,
} from "../fixtures/observation-completion.mjs";
import { shotPath } from "../fixtures/shots.mjs";

for (const [label, width, height] of [
  ["wide", 1440, 1080],
  ["mobile", 390, 844],
  ["short", 900, 648],
])
  test(`location clarification stays graphical and completed results survive retry on ${label}`, async ({
    page,
    request,
  }) => {
    const model = await createObservationCompletionModel();
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "local-observation-completion",
            },
          })
        ).ok(),
      ).toBe(true);
      await page.setViewportSize({ width, height });
      await page.goto("/");
      await verifyObservationCompletion(page, model, {
        shot: shotPath(`76-${label}-location-clarification.png`),
      });
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
