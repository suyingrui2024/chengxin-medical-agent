// Failure injection into a real renderer; directory data is a synthetic host fixture.
import { expect } from "@playwright/test";

export async function verifyDirectoryRecovery(page) {
  const browser = page.locator("#execution-file-browser");
  if (!(await browser.evaluate((node) => node.open)))
    await browser.locator(":scope > summary").click();
  const input = page.getByLabel("描述执行任务");
  await input.fill("目录恢复时保留这份未发送的任务");
  await page.locator("#execution-files button").first().focus();
  let reads = 0,
    held;
  const pattern = "**/api/execution/focus-a/directory?**";
  const failure = async (route) => {
    reads++;
    if (reads === 1)
      await route.fulfill({
        status: 503,
        json: {
          error: {
            message: '<img src=x onerror="window.DIRECTORY_INJECTED=1">',
          },
        },
      });
    else held = route;
  };
  await page.route(pattern, failure);
  try {
    const retry = page.locator("#execution-directory-retry");
    await expect(retry).toBeVisible();
    await expect(page.locator("#execution-directory-status")).toContainText(
      "上次列表",
    );
    await expect(page.locator("#execution-files button")).toHaveCount(4);
    await expect(
      page.locator("#execution-files button").first(),
    ).toBeDisabled();
    await expect(retry).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(retry).toHaveAttribute("aria-disabled", "true");
    await page.keyboard.press("Enter");
    await expect.poll(() => reads).toBe(2);
    await held.fallback();
    await expect(retry).toBeHidden();
    await expect(page.locator("#execution-files button").first()).toBeEnabled();
    await expect(page.locator("#execution-files")).toBeFocused();
    await expect(input).toHaveValue("目录恢复时保留这份未发送的任务");
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    expect(
      await page.evaluate(() => window.DIRECTORY_INJECTED),
    ).toBeUndefined();
    return {
      passed: true,
      host_snapshot_fixture: true,
      read_failure_and_manual_retry: true,
      cached_list_marked_and_disabled: true,
      draft_and_preview_preserved: true,
    };
  } finally {
    await page.unroute(pattern, failure);
  }
}
