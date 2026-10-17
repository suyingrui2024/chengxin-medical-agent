import { test, expect } from "@playwright/test";
import {
  createAssertionModel,
  verifyAssertionFlow,
} from "../fixtures/assertion-flow.mjs";

test("uncertain denials are rejected atomically, repaired and preserved through patient correction", async ({
  page,
  request,
}) => {
  const model = await createAssertionModel();
  try {
    expect(
      (
        await request.post("/api/settings", {
          data: {
            backend: "compatible",
            base_url: model.endpoint,
            model: "fixture",
            api_key: "local-assertion-fixture",
          },
        })
      ).ok(),
    ).toBe(true);
    await page.goto("/");
    await verifyAssertionFlow(page, model);
  } finally {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await model.close();
  }
});
