import { chromium } from "@playwright/test";
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto("http://127.0.0.1:19011/");
await page.waitForTimeout(1500);
await page.getByRole("button", { name: "开始问诊", exact: true }).click().catch(() => {});
await page.waitForTimeout(1000);
// 溢出检测
const overflow = await page.evaluate(() => {
  const bad = [];
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.right > window.innerWidth + 2 && r.width > 40 && el.offsetParent) {
      bad.push(`${el.tagName}.${String(el.className).slice(0, 40)} right=${Math.round(r.right)}`);
    }
  }
  return bad.slice(0, 6);
});
console.log("溢出元素:", overflow.length ? overflow : "无");
await page.screenshot({ path: "screenshots/r2-40-mobile-consultation.png", fullPage: true });
await page.locator('[data-view="records"]').click().catch(() => {});
await page.waitForTimeout(900);
await page.screenshot({ path: "screenshots/r2-41-mobile-records.png", fullPage: true });
await browser.close();
