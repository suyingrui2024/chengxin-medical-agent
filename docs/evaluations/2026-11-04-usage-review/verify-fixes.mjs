import { chromium } from "@playwright/test";
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto("http://127.0.0.1:19011/");
await page.getByRole("button", { name: "开始问诊", exact: true }).click();
await page.getByRole("button", { name: "我自己", exact: true }).click();
await page.getByRole("textbox", { name: "输入你的描述" }).fill("32岁");
await page.getByRole("button", { name: "发送回答" }).click();
await page.waitForTimeout(1200);
// F3：中途刷新应自动回到进行中的会话
await page.reload();
await page.waitForTimeout(2000);
const phase = await page.locator("#phase-label").textContent().catch(() => null);
const welcomeHidden = await page.locator("#welcome").isHidden().catch(() => null);
console.log("F3 刷新后 phase:", phase?.trim(), "| welcome 隐藏:", welcomeHidden);
// F4：需要到达摘要才能看折叠区——跳过，改由走查截图确认
await browser.close();
