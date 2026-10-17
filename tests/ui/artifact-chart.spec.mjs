import { test, expect } from "@playwright/test";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

async function openTable(page, rows, extra = {}) {
  const fixture = await installExecutionFocusFixture(page);
  await page.route("**/api/execution/*/preview?**", async (route) => {
    const path = new URL(route.request().url()).searchParams.get("path");
    await route.fulfill({
      json: {
        schema_version: "execution-artifact/v1",
        path,
        kind: "table",
        revision: "fixture-1",
        sha256: "a".repeat(64),
        size: 80,
        text: "sample,mass\nA,1250",
        rows,
        columns: rows[0].length,
        total_rows: rows.length,
        ...extra,
      },
    });
  });
  await page.goto("/");
  await openFocusWorkspace(page);
  await page
    .getByRole("button", { name: "快速查看：results.csv", exact: true })
    .click();
  return fixture;
}

test("tables switch to zero-based charts, keep exact values and retain the selected column in the expanded view", async ({
  page,
}) => {
  await openTable(page, [
    ["样本", "质量 (g)", "差值 (g)"],
    ["A", "1250", "-2"],
    ["B", "2000", "4"],
    ["C", "750", "0"],
  ]);
  await expect(page.locator("#execution-preview table")).toBeVisible();
  await page.getByRole("button", { name: "查看图表", exact: true }).click();
  await expect(
    page.locator("#execution-preview .artifact-chart-row"),
  ).toHaveCount(3);
  await expect(
    page.locator("#execution-preview .artifact-chart-value"),
  ).toHaveText(["1250", "2000", "750"]);
  await page.getByLabel("图表数值列").selectOption("2");
  await expect(page.getByLabel("图表数值列")).toBeFocused();
  await expect(
    page.locator("#execution-preview .artifact-chart-value"),
  ).toHaveText(["-2", "4", "0"]);
  const widths = await page
    .locator("#execution-preview .artifact-chart-bar")
    .evaluateAll((nodes) => nodes.map((n) => parseFloat(n.style.width)));
  for (const [index, expected] of [100 / 3, 200 / 3, 0].entries())
    expect(widths[index]).toBeCloseTo(expected, 3);
  await page.locator("#execution-preview-expand").click();
  await expect(
    page.locator("#execution-artifact-large .artifact-chart-column"),
  ).toHaveValue("2");
  await page
    .locator("#execution-artifact-large .artifact-chart-column")
    .selectOption("1");
  await page.locator("#execution-artifact-close").click();
  await expect(
    page.locator("#execution-preview .artifact-chart-column"),
  ).toHaveValue("1");
  await page.getByRole("button", { name: "查看原文", exact: true }).click();
  await expect(page.locator("#execution-preview pre")).toContainText(
    "sample,mass",
  );
  await page.getByRole("button", { name: "查看表格", exact: true }).click();
  await expect(page.locator("#execution-preview table")).toBeVisible();
});

test("missing, formula and unsafe numeric columns do not become charts", async ({
  page,
}) => {
  await openTable(page, [
    ["name", "mixed", "formula", "identifier"],
    ["A", "1", "=1+1", "9007199254740993"],
    ["B", "", "2", "9007199254740994"],
  ]);
  await expect(page.locator("#execution-preview-chart")).toBeHidden();
  await expect(page.locator("#execution-preview table")).toContainText("=1+1");
});

test("truncation and untrusted labels stay explicit and inert", async ({
  page,
}) => {
  await openTable(
    page,
    [
      ["name", "value"],
      ["<img src=x onerror=window.CHART_INJECTED=1>", "2"],
      ["B", "-1"],
    ],
    { rows_truncated: true, total_rows: 300 },
  );
  await page.getByRole("button", { name: "查看图表", exact: true }).click();
  await expect(page.locator(".artifact-chart-caption")).toContainText(
    "仅显示当前预览的 2 条数据",
  );
  await expect(page.locator("#execution-preview-meta")).toContainText(
    "仅显示前100行",
  );
  await expect(page.locator(".artifact-chart-label").first()).toContainText(
    "<img",
  );
  await expect(page.locator("#execution-preview img")).toHaveCount(0);
  expect(await page.evaluate(() => window.CHART_INJECTED)).toBeUndefined();
});

test("long previews scroll with the keyboard without changing row order", async ({
  page,
}) => {
  await openTable(page, [
    ["sample", "value"],
    ...Array.from({ length: 99 }, (_, i) => [`样本 ${i + 1}`, String(i)]),
  ]);
  await page.getByRole("button", { name: "查看图表", exact: true }).click();
  const list = page.locator(".artifact-chart-points");
  await page.evaluate(() => {
    const sheet = [...document.styleSheets].find((s) =>
      s.href.endsWith("artifact-chart.css"),
    );
    sheet.insertRule(
      ".artifact-chart-scroll::-webkit-scrollbar {width:17px}",
      sheet.cssRules.length,
    );
  });
  const alignment = await page
    .locator("#execution-preview")
    .evaluate((root) => {
      const scale = root
        .querySelector(".artifact-chart-range")
        .getBoundingClientRect();
      const bar = root
        .querySelector(".artifact-chart-track")
        .getBoundingClientRect();
      return {
        left: Math.abs(scale.left - bar.left),
        right: Math.abs(scale.right - bar.right),
      };
    });
  expect(alignment.left).toBeLessThan(1);
  expect(alignment.right).toBeLessThan(1);
  await expect(list.locator("li")).toHaveCount(99);
  const scroller = page.locator(".artifact-chart-scroll");
  await scroller.focus();
  await page.keyboard.press("End");
  await expect
    .poll(() => scroller.evaluate((n) => n.scrollTop))
    .toBeGreaterThan(0);
  await expect(list.locator("li").last()).toContainText("样本 99");
});

test("changed files retain the snapshot warning and switching workspaces clears the chart", async ({
  page,
}) => {
  const fixture = await openTable(page, [
    ["sample", "value"],
    ["A", "1250"],
  ]);
  await page.getByRole("button", { name: "查看图表", exact: true }).click();
  await page.route("**/api/execution/*/artifact-status?**", (route) =>
    route.fulfill({
      json: {
        schema_version: "artifact-freshness/v1",
        path: "results.csv",
        matches: false,
      },
    }),
  );
  await expect(page.locator("#execution-preview-meta")).toContainText(
    "文件已变化",
  );
  await expect(page.locator(".artifact-chart-value")).toHaveText("1250");
  fixture.first.status = "completed";
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  await page.locator("#execution-workspace").selectOption("focus-b");
  await expect(page.locator(".artifact-chart")).toHaveCount(0);
  await expect(page.locator("#execution-preview-controls")).toBeHidden();
});

for (const [name, width, height] of [
  ["wide", 1440, 1080],
  ["mobile", 390, 844],
  ["small", 320, 640],
]) {
  test(`chart rows remain readable in six themes on ${name}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height });
    await openTable(page, [
      ["样本", "质量 (g)"],
      ["样本 A", "1250"],
      ["样本 B", "2000"],
      ["样本 C", "750"],
    ]);
    await page.getByRole("button", { name: "查看图表", exact: true }).click();
    for (const theme of ["forest", "ocean", "iris", "sand", "slate", "night"]) {
      await page.evaluate(
        (theme) => (document.documentElement.dataset.theme = theme),
        theme,
      );
      const boxes = await page
        .locator("#execution-preview .artifact-chart-row")
        .evaluateAll((nodes) =>
          nodes.map((n) => {
            const b = n.getBoundingClientRect();
            return {
              left: b.left,
              right: b.right,
              font: parseFloat(getComputedStyle(n).fontSize),
            };
          }),
        );
      expect(
        boxes.every((b) => b.left >= 0 && b.right <= width && b.font >= 12),
      ).toBe(true);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page
        .locator("#execution-preview")
        .screenshot({ path: info.outputPath(`${name}-${theme}.png`) });
    }
  });
}
