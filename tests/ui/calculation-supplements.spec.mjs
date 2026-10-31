import { test, expect } from "@playwright/test";
import {
  createCalculationSupplementsModel,
  verifyCalculationSupplements,
} from "../fixtures/calculation-supplements.mjs";
import { shotPath } from "../fixtures/shots.mjs";

for (const mobile of [false, true])
  test(`calculation supplements survive retries and later corrections${mobile ? " on a narrow screen" : ""}`, async ({
    page,
    request,
  }) => {
    const model = await createCalculationSupplementsModel();
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "calculation-supplements-ui",
            },
          })
        ).ok(),
      ).toBe(true);
      await page.goto("/");
      await verifyCalculationSupplements(page, model, {
        shots: shotPath(
          `66-${mobile ? "mobile" : "wide"}-calculation-supplement`,
        ),
        mobile,
      });
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
