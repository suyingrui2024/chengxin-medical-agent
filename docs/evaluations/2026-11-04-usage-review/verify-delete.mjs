import { chromium } from "@playwright/test";
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
let dialogSeen = null;
page.on("dialog", async (d) => { dialogSeen = d.message(); await d.dismiss(); });
await page.goto("http://127.0.0.1:19011/");
await page.locator('[data-view="history"]').click();
await page.waitForTimeout(1200);
const del = page.getByText("删除", { exact: true }).first();
if (await del.isVisible().catch(() => false)) {
  await del.click();
  await page.waitForTimeout(1200);
  const stillThere = await page.getByText("删除", { exact: true }).count();
  console.log("点击删除后：原生 dialog =", JSON.stringify(dialogSeen), "| 删除按钮仍在:", stillThere > 0, "| 条目数:", await page.locator(".history-card, [class*=history] [class*=card]").count());
} else {
  console.log("删除按钮不可见");
}
await browser.close();
