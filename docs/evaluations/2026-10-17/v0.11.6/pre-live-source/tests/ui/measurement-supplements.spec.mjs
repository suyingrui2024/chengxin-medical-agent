import { test, expect } from "@playwright/test";
import {
  createMeasurementSupplementsModel,
  verifyMeasurementSupplements,
} from "../fixtures/measurement-supplements.mjs";

for (const narrow of [false, true])
  test(`full measurement reports retain medication updates through failure and retry${narrow ? " on a narrow screen" : ""}`, async ({
    page,
    request,
  }) => {
    const model = await createMeasurementSupplementsModel();
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "local-supplements",
            },
          })
        ).ok(),
      ).toBe(true);
      if (narrow) await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/");
      await verifyMeasurementSupplements(
        page,
        model,
        `docs/assets/ui/${narrow ? "63-measurement-supplement-narrow" : "62-measurement-supplement"}`,
      );
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
