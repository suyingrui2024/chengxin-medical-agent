import { test, expect } from "@playwright/test";
import {
  createPresentProvenanceModel,
  verifyPresentProvenance,
} from "../fixtures/present-provenance.mjs";
import { shotPath } from "../fixtures/shots.mjs";

for (const mobile of [false, true]) {
  test(`recorded reports expose scoped original evidence without changing facts${mobile ? " on a narrow screen" : ""}`, async ({
    page,
    request,
  }) => {
    const model = await createPresentProvenanceModel();
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "local-provenance",
            },
          })
        ).ok(),
      ).toBe(true);
      if (mobile) await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/");
      await verifyPresentProvenance(
        page,
        model,
        shotPath(
          `${mobile ? "56-present-provenance-narrow" : "55-present-provenance"}.png`,
        ),
      );
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
}

test("recorded evidence is inert, malformed sources fall back and old summaries remain readable", async ({
  page,
}) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const { createRenderer } = await import("/structured-ui.js");
    const renderer = createRenderer();
    const evidence = '<img src=x onerror="window.KNOWN_EXECUTED=1">已报告';
    const item = {
      key: "medications_taken",
      label: "用药与处理",
      value: "已报告",
      evidence,
      source: "patient",
      quality: "not_assessed",
      context: {
        scope: "source_turn",
        turn_id: "local-turn",
        text:
          evidence + '，用量不清楚<svg onload="window.KNOWN_EXECUTED=1"></svg>',
      },
    };
    const base = {
      known: [item],
      unknown: [],
      care_route: "合成展示检查",
      return_now_if: [],
    };
    const host = document.createElement("div");
    host.id = "known-contract";
    host.style.cssText =
      "position:fixed;inset:0;z-index:100;background:white;overflow:auto";
    for (const [id, entry] of [
      ["safe", item],
      [
        "repeated",
        {
          ...item,
          context: { ...item.context, text: evidence + "；" + evidence },
        },
      ],
      ["old", { key: item.key, label: item.label, value: "旧记录" }],
      ["bad", { ...item, evidence: {} }],
      [
        "mismatch",
        { ...item, context: { ...item.context, text: "不匹配的原话" } },
      ],
      ["wrong-source", { ...item, source: "device" }],
    ]) {
      const row = document.createElement("div");
      row.id = `known-${id}`;
      row.append(
        renderer.render(
          { id, type: "summary", data: { ...base, known: [entry] } },
          { readOnly: true },
        ),
      );
      host.append(row);
    }
    document.body.append(host);
  });
  await page.locator("#known-safe .summary-provenance > summary").click();
  await page.locator("#known-safe .summary-source-context > summary").click();
  await expect(
    page.locator("#known-safe .summary-source-context blockquote"),
  ).toContainText("用量不清楚");
  await expect(page.locator("#known-safe .source-evidence-match")).toHaveCount(
    1,
  );
  await page.locator("#known-repeated .summary-provenance > summary").click();
  await page
    .locator("#known-repeated .summary-source-context > summary")
    .click();
  await expect(page.locator("#known-repeated mark")).toHaveCount(0);
  await expect(
    page.locator(
      "#known-contract img, #known-contract script, #known-contract blockquote svg",
    ),
  ).toHaveCount(0);
  expect(await page.evaluate(() => window.KNOWN_EXECUTED)).toBeUndefined();
  await expect(
    page.locator("#known-contract .structured-fallback"),
  ).toHaveCount(3);
  await expect(page.locator("#known-old .summary-item strong")).toHaveText(
    "旧记录",
  );
  await expect(page.locator("#known-contract button")).toHaveCount(0);
});
