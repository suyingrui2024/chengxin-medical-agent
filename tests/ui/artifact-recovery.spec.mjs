import { test, expect } from "@playwright/test";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

test("a stalled preview times out and allows an explicit reread", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  let held,
    reads = 0;
  await page.route("**/api/execution/focus-a/preview?**", async (route) => {
    if (!reads++) held = route;
    else await route.fallback();
  });
  await page
    .getByRole("button", { name: "快速查看：results.csv", exact: true })
    .click();
  await expect.poll(() => Boolean(held)).toBe(true);
  const reload = page.locator("#execution-preview-reload");
  await expect(reload).toBeEnabled({ timeout: 18000 });
  await expect(page.locator("#execution-preview")).toContainText("超时");
  expect(reads).toBe(1);
  await reload.click();
  await expect(page.locator("#execution-preview table")).toContainText("1250");
  expect(reads).toBe(2);
  expect(fixture.answers).toEqual([]);
  await held.abort().catch(() => {});
});

test("a stalled freshness check releases its slot and preserves the snapshot for copying", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  let held,
    checks = 0;
  await page.route(
    "**/api/execution/focus-a/artifact-status?**",
    async (route) => {
      if (!checks++) held = route;
      else await route.fallback();
    },
  );
  await page
    .getByRole("button", { name: "快速查看：results.csv", exact: true })
    .click();
  await expect(page.locator("#execution-preview table")).toContainText("1250");
  await page.locator("#execution-preview-expand").click();
  await expect(page.locator("#execution-artifact-dialog")).toHaveAttribute(
    "open",
    "",
  );
  await expect.poll(() => Boolean(held), { timeout: 5000 }).toBe(true);
  await expect(page.locator("#execution-preview-meta")).toContainText(
    "未能重新核对",
    { timeout: 18000 },
  );
  await expect(page.locator("#execution-artifact-dialog")).not.toHaveAttribute(
    "open",
    "",
  );
  await expect(page.locator("#execution-preview table")).toContainText("1250");
  await expect(page.locator("#execution-preview-copy")).toBeEnabled();
  await expect.poll(() => checks, { timeout: 5000 }).toBeGreaterThanOrEqual(2);
  await expect(page.locator("#execution-preview-meta")).toContainText(
    "读取快照",
  );
  expect(fixture.answers).toEqual([]);
  await held.abort().catch(() => {});
});

for (const destination of ["file", "workspace"]) {
  test(`switching ${destination} cancels an outstanding preview and rejects its late result`, async ({
    page,
  }) => {
    const fixture = await installExecutionFocusFixture(page);
    await page.goto("/");
    await openFocusWorkspace(page);
    fixture.first.status = "completed";
    await expect(page.locator("#execution-focus-label")).toHaveText(
      "本轮已完成",
    );
    let held;
    await page.route(
      "**/api/execution/focus-a/preview?path=results.csv",
      (route) => {
        held = route;
      },
    );
    await page
      .getByRole("button", { name: "快速查看：results.csv", exact: true })
      .click();
    await expect.poll(() => Boolean(held)).toBe(true);
    const aborted = page.waitForEvent("requestfailed", (request) =>
      request.url().includes("/preview?path=results.csv"),
    );
    if (destination === "workspace")
      await page.locator("#execution-workspace").selectOption("focus-b");
    else
      await page
        .getByRole("button", { name: "快速查看：notes<draft>.md", exact: true })
        .click();
    await aborted;
    await held
      .fulfill({
        json: {
          schema_version: "execution-artifact/v1",
          kind: "text",
          path: "results.csv",
          revision: "late",
          sha256: "b".repeat(64),
          size: 4,
          text: "LATE_OLD_PREVIEW",
        },
      })
      .catch(() => {});
    if (destination === "workspace")
      await expect(page.locator("#execution-preview-controls")).toBeHidden();
    else {
      await expect(page.locator("#execution-preview-title")).toHaveText(
        "notes<draft>.md",
      );
      await expect(page.locator("#execution-preview")).toContainText("<img");
      await expect(page.locator("#execution-preview-reload")).toBeEnabled();
    }
    await expect(page.locator("#execution-preview")).not.toContainText(
      "LATE_OLD_PREVIEW",
    );
    expect(fixture.answers).toEqual([]);
  });
}

test("a stalled image export times out without downloading and permits retry", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  await page
    .getByRole("button", { name: "快速查看：chart.png", exact: true })
    .click();
  await expect(page.locator("#execution-preview img")).toBeVisible();
  let held,
    downloads = 0;
  page.on("download", () => downloads++);
  await page.route("**/api/execution/focus-a/image?**", (route) => {
    held = route;
  });
  await page.locator("#execution-preview-save").click();
  await expect.poll(() => Boolean(held)).toBe(true);
  await expect(page.locator("#execution-preview-action-status")).toContainText(
    "超时",
    { timeout: 18000 },
  );
  await expect(page.locator("#execution-preview-save")).toBeEnabled();
  expect(downloads).toBe(0);
  await held.abort().catch(() => {});
  expect(fixture.answers).toEqual([]);
});
