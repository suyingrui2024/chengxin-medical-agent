import { expect } from "@playwright/test";

// Host snapshot fixtures exercise native rendering and GET recovery, not a model.
export async function verifyArtifactRecovery(page) {
  const quickLook = page.locator("#execution-quick-look");
  if (!(await quickLook.evaluate((node) => node.open)))
    await quickLook.locator(":scope > summary").click();
  const previewPattern = "**/api/execution/focus-a/preview?path=results.csv";
  const statusPattern = "**/api/execution/focus-a/artifact-status?**";
  let previews = 0,
    checks = 0;
  const preview = async (route) => {
    if (!previews++)
      await route.fulfill({
        status: 503,
        json: { error: { message: "临时文件读取失败：恢复检查" } },
      });
    else await route.fallback();
  };
  const freshness = async (route) => {
    if (!checks++)
      await route.fulfill({
        status: 503,
        json: { error: { message: "临时快照核对失败：恢复检查" } },
      });
    else await route.fallback();
  };
  await page.route(previewPattern, preview);
  try {
    await page
      .getByRole("button", { name: "快速查看：results.csv", exact: true })
      .click();
    await expect(page.locator("#execution-preview")).toContainText(
      "临时文件读取失败",
    );
    await expect(page.locator("#execution-preview-reload")).toBeEnabled();
    await page.locator("#execution-preview-reload").click();
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    expect(previews).toBe(2);
    await page.route(statusPattern, freshness);
    await expect(page.locator("#execution-preview-meta")).toContainText(
      "未能重新核对",
      { timeout: 5000 },
    );
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    await expect(page.locator("#execution-preview-copy")).toBeEnabled();
    await expect
      .poll(() => checks, { timeout: 5000 })
      .toBeGreaterThanOrEqual(2);
    await expect(page.locator("#execution-preview-meta")).toContainText(
      "读取快照",
    );
    return {
      passed: true,
      host_snapshot_fixture: true,
      preview_failure_and_manual_retry: true,
      freshness_failure_and_recovery: true,
      snapshot_preserved: true,
      actual_timeout_scope:
        "Separate browser probes hold real requests beyond 15 seconds; native checks inject HTTP 503.",
      external_model_calls: 0,
    };
  } finally {
    await page.unroute(previewPattern, preview);
    await page.unroute(statusPattern, freshness);
  }
}
