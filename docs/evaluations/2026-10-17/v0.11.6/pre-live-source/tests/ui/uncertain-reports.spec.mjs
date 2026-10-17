import { test, expect } from "@playwright/test";
import { exerciseUncertainReports } from "../fixtures/uncertainty-flow.mjs";

test("unresolved narratives remain visible and editable through a complete consultation", async ({
  page,
}) => {
  await page.goto("/");
  await exerciseUncertainReports(
    page,
    "docs/assets/ui/53-uncertain-reports.png",
  );
});

test("a narrow patient window preserves reports and usable correction controls", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await exerciseUncertainReports(
    page,
    "docs/assets/ui/54-uncertain-reports-narrow.png",
  );
});

test("unresolved reports render inertly, validate their contract and preserve older summaries", async ({
  page,
}) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const { createRenderer } = await import("/structured-ui.js");
    const renderer = createRenderer();
    const report = {
      key: "allergies",
      label: "过敏信息",
      evidence: '<img src=x onerror="window.REPORT_EXECUTED=1">是否过敏不确定',
      source: "patient",
      quality: "not_assessed",
    };
    const summary = {
      known: [],
      unknown: ["过敏信息"],
      care_route: "合成展示检查",
      return_now_if: [],
      unknown_details: [report],
    };
    const host = document.createElement("div");
    host.id = "uncertainty-contract";
    for (const [id, data] of [
      ["safe", summary],
      [
        "context",
        {
          ...summary,
          unknown_details: [
            {
              ...report,
              context: {
                scope: "source_turn",
                turn_id: "synthetic-turn",
                text: `以前有过皮疹；${report.evidence}；还提供了其他信息`,
              },
            },
          ],
        },
      ],
      [
        "bad-context",
        {
          ...summary,
          unknown_details: [
            {
              ...report,
              context: {
                scope: "source_turn",
                turn_id: "synthetic-turn",
                text: "不属于此引文的原话",
              },
            },
          ],
        },
      ],
      ["legacy", { ...summary, unknown_details: undefined }],
      [
        "invalid",
        { ...summary, unknown_details: [{ ...report, source: "invented" }] },
      ],
      [
        "mismatch",
        { ...summary, unknown_details: [{ ...report, label: "其他字段" }] },
      ],
      [
        "long",
        {
          ...summary,
          unknown_details: [{ ...report, evidence: "仍不清楚。".repeat(100) }],
        },
      ],
    ]) {
      const row = document.createElement("div");
      row.id = `report-${id}`;
      row.append(
        renderer.render({ id, type: "summary", data }, { readOnly: true }),
      );
      host.append(row);
    }
    document.body.append(host);
  });
  const host = page.locator("#uncertainty-contract");
  await expect(host.locator("img,script")).toHaveCount(0);
  expect(await page.evaluate(() => window.REPORT_EXECUTED)).toBeUndefined();
  await expect(page.locator("#report-safe blockquote")).toContainText(
    "是否过敏不确定",
  );
  await expect(page.locator("#report-legacy .unknown-items")).toHaveText(
    "过敏信息",
  );
  await expect(host.locator(".structured-fallback")).toHaveCount(3);
  const context = page.locator("#report-context details");
  await expect(context.locator("blockquote")).toBeHidden();
  await context.locator("summary").click();
  await expect(context).toContainText("以前有过皮疹");
  await expect(context).toContainText("尚未全部归入此字段");
  await expect(host.locator("button")).toHaveCount(0);
  await expect(page.locator("#report-long blockquote")).toBeHidden();
  await page.locator("#report-long summary").click();
  await expect(page.locator("#report-long blockquote")).toHaveText(
    "仍不清楚。".repeat(100),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
