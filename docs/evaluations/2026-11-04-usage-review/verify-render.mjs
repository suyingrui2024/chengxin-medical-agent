import { chromium } from "@playwright/test";
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto("http://127.0.0.1:19011/");
// 打开蓝画像那个已确认的会话（最近问诊列表第一条）
await page.locator(".recent-session, [class*=recent]").first().click().catch(() => {});
await page.waitForTimeout(1500);
const info = await page.evaluate(() => {
  const out = {};
  for (const h of document.querySelectorAll("h3, h2, strong, [class*=supplement-title], [class*=section]")) {
    const t = h.textContent?.trim() ?? "";
    if (t.includes("现在能做的事") || t.includes("请立即求助")) {
      const next = h.nextElementSibling;
      out[t.slice(0, 20)] = next
        ? { tag: next.tagName, cls: next.className, text: (next.textContent ?? "").slice(0, 220) }
        : "无后继元素";
    }
  }
  return out;
});
console.log(JSON.stringify(info, null, 1));
await browser.close();
