import { test, expect } from "@playwright/test";
import fs from "node:fs";

const catalog = JSON.parse(
  fs.readFileSync(
    new URL("../fixtures/clinician-capabilities.json", import.meta.url),
    "utf8",
  ),
);

// The clinician half has no patient card: these run through the
// "能力与模板" preview view, which builds its inputs straight from the
// pack's input_schema. Nothing had ever driven it in a browser, so a
// broken layout or an unusable field would have shipped unseen.
for (const target of catalog) {
  test(`${target.id} previews in the capability view`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/");
    // The capability view is developer-only. Switch through the real
    // control rather than poking data-mode, or the app's own state and
    // the styles disagree.
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByLabel("界面模式").selectOption("developer");
    // Toggle the panel shut: leaving it open covers the run button
    // on taller forms.
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.locator('button[data-view="capabilities"]').click();

    // The catalog is fetched after load. The first case in the file pays
    // the cold-start cost, so wait generously rather than race it.
    await expect(page.locator("#capability-select option")).not.toHaveCount(0, {
      timeout: 30000,
    });
    await expect(
      page.locator(`#capability-select option[value="${target.id}"]`),
    ).toHaveCount(1, { timeout: 30000 });
    await page.selectOption("#capability-select", target.id);

    // Every declared field must reach the form.
    const fields = page.locator(
      "#capability-inputs input, #capability-inputs select",
    );
    await expect(fields).toHaveCount(target.size);

    const values = Object.values(target.sample || {});
    for (let index = 0; index < values.length; index += 1) {
      const field = fields.nth(index);
      if ((await field.evaluate((node) => node.tagName)) === "SELECT") {
        await field.selectOption(String(values[index]));
      } else {
        await field.fill(String(values[index]));
      }
    }

    // Long forms scroll the run button under the settings drawer; a
    // plain click waits forever for an unoccluded point.
    await page.locator("#run-capability-test").scrollIntoViewIfNeeded();
    await page.locator("#run-capability-test").click({ force: true });
    const result = page.locator("#capability-test-result");
    await expect(result).not.toBeEmpty();
    // A preview is an engineering check, and the page must say so.
    await expect(
      page.locator("#capability-test-feedback, .field-hint").first(),
    ).toBeVisible();

    // The view must remain usable on a laptop-width window.
    await page.setViewportSize({ width: 1024, height: 768 });
    const width = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      viewport: innerWidth,
    }));
    expect(width.scroll).toBeLessThanOrEqual(width.viewport);
  });
}
