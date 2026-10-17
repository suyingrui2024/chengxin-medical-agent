import { test, expect } from "@playwright/test";
import {
  createRepeatedObservationModel,
  verifyRepeatedObservations,
} from "../fixtures/repeated-observations.mjs";

for (const mobile of [false, true]) {
  test(`repeated temperatures retain history, distinguish skipped attempts and reassess risk${mobile ? " on a narrow screen" : ""}`, async ({
    page,
    request,
  }) => {
    const model = await createRepeatedObservationModel();
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "local-observations",
            },
          })
        ).ok(),
      ).toBe(true);
      if (mobile) await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/");
      await verifyRepeatedObservations(
        page,
        model,
        `docs/assets/ui/${mobile ? "58-repeated-observation-narrow" : "57-repeated-observation"}`,
      );
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
}

test("observation rendering rejects malformed histories and treats evidence as inert text", async ({
  page,
}) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const { createRenderer } = await import("/structured-ui.js");
    const renderer = createRenderer();
    const series = {
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
          evidence: '<img src=x onerror="window.OBSERVATION_EXECUTED=1">',
        },
      ],
    };
    const host = document.createElement("div");
    host.id = "observation-contract";
    for (const [id, data] of [
      ["valid", series],
      [
        "wrong-type",
        { ...series, entries: [{ ...series.entries[0], value: "37.6" }] },
      ],
      [
        "duplicate",
        {
          ...series,
          total: 2,
          entries: [...series.entries, ...series.entries],
        },
      ],
      ["bad-status", { ...series, latest_status: "skipped" }],
      [
        "no-current",
        { ...series, entries: [{ ...series.entries[0], current: false }] },
      ],
    ]) {
      const section = document.createElement("section");
      section.id = id;
      section.append(renderer.render({ id, type: "observation_series", data }));
      host.append(section);
    }
    document.body.append(host);
  });
  await expect(page.locator("#valid .observation-card")).toHaveCount(1);
  await expect(page.locator("#valid img")).toHaveCount(0);
  expect(
    await page.evaluate(() => window.OBSERVATION_EXECUTED),
  ).toBeUndefined();
  for (const id of ["wrong-type", "duplicate", "bad-status", "no-current"])
    await expect(page.locator(`#${id} .structured-fallback`)).toHaveCount(1);
});
