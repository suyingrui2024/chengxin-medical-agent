import { test, expect } from "@playwright/test";
import {
  createPatientCalculationModel,
  verifyPatientCalculation,
} from "../fixtures/patient-calculation.mjs";

test("patient calculation confirms editable inputs, recovers a lost response and stays readable on mobile", async ({
  page,
  request,
}) => {
  const model = await createPatientCalculationModel();
  try {
    expect(
      (
        await request.post("/api/settings", {
          data: {
            backend: "compatible",
            base_url: model.endpoint,
            model: "fixture",
            api_key: "patient-test-only",
          },
        })
      ).ok(),
    ).toBe(true);
    await page.goto("/");
    await verifyPatientCalculation(page, model, {
      shots: "docs/assets/ui",
      mobile: true,
    });
  } finally {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await model.close();
  }
});
