/**
 * 界面模式 lives inside the settings view since 8629351 collapsed the
 * settings dialog into a full page.
 *
 * Specs that still selected it from the consultation screen did not fail
 * loudly — the control is in the DOM, so `selectOption` simply waited out
 * its 30s actionability timeout. That is why so much of the browser
 * suite went red without anyone noticing: every failure looked like a
 * slow test.
 *
 * The helper restores the pre-8629351 post-condition: the caller ends up
 * back on the consultation view with the new mode applied.
 */

/** Narrow viewports collapse the sidebar; open it only if it is shut. */
async function ensureSidebarOpen(page) {
  const menu = page.locator("#mobile-menu");
  if (!(await menu.isVisible())) return;
  const open = await page
    .locator("#sidebar")
    .evaluate((node) => node.classList.contains("open"));
  if (!open) await menu.click();
}

export async function setInterfaceMode(page, mode) {
  // Every navigate() closes the drawer, so it is reopened before each nav
  // click rather than once for the whole helper.
  await ensureSidebarOpen(page);
  await page.locator('.nav-item[data-view="settings"]').click();
  await page.getByLabel("界面模式").selectOption(mode);
  await ensureSidebarOpen(page);
  await page.locator('.nav-item[data-view="consultation"]').click();
}
