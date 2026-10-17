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

test("supplement evidence stays inert and invalid processing status falls back", async ({
  page,
}) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const { createRenderer } = await import("/structured-ui.js");
    const renderer = createRenderer();
    const evidence = '<img src=x onerror="window.SUPPLEMENT_EXECUTED=1">';
    const base = {
      task_id: "axillary_temperature",
      key: "temperature_c",
      title: "腋温记录",
      unit: "℃",
      can_repeat: false,
      total: 1,
      latest_status: "recorded",
      entries: [
        {
          id: "one",
          status: "recorded",
          value: 37.6,
          current: true,
          source: "patient",
          quality: "not_assessed",
          recorded_at: "2026-10-17T10:00:00Z",
          evidence: "腋温37.6℃",
        },
      ],
      supplement: { text: evidence, status: "forwarded" },
    };
    const host = document.createElement("div");
    host.id = "supplement-contract";
    for (const [id, data] of [
      ["safe", base],
      [
        "bad-status",
        { ...base, supplement: { text: evidence, status: "verified" } },
      ],
      [
        "bad-text",
        {
          ...base,
          supplement: { text: { html: evidence }, status: "forwarded" },
        },
      ],
    ]) {
      const section = document.createElement("section");
      section.id = `supplement-${id}`;
      section.append(renderer.render({ id, type: "observation_series", data }));
      host.append(section);
    }
    document.body.append(host);
  });
  await expect(
    page.locator("#supplement-safe .observation-supplement"),
  ).toContainText("已带入本轮问诊");
  await expect(page.locator("#supplement-safe img")).toHaveCount(0);
  expect(await page.evaluate(() => window.SUPPLEMENT_EXECUTED)).toBeUndefined();
  for (const name of ["bad-status", "bad-text"])
    await expect(
      page.locator(`#supplement-${name} .structured-fallback`),
    ).toHaveCount(1);
});
