import { chromium } from "@playwright/test";
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage();
await page.goto("http://127.0.0.1:19011/");
await page.getByRole("button", { name: "开始问诊", exact: true }).click();
const input = page.getByRole("textbox", { name: "输入你的描述" });
const maxlength = await input.evaluate((el) => el.getAttribute("maxlength"));
await input.fill("短");
const shortCount = await page.locator("#input-count").textContent();
// 2500 字 → 提示出现
await input.fill("述".repeat(2500));
const midCount = await page.locator("#input-count").textContent();
const midHint = await page.locator("#composer-length-hint").isVisible();
// 20000 字 → 仍可输入（无 maxlength 拦截），提示保持
await input.fill("述".repeat(20000));
const bigCount = await page.locator("#input-count").textContent();
console.log("maxlength 属性:", maxlength);
console.log("短文本:", shortCount.trim());
console.log("2500字:", midCount.trim(), "| 提示:", midHint);
console.log("20000字:", bigCount.trim());
await browser.close();
