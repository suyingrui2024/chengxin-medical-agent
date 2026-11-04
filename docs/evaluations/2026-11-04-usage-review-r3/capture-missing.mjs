/**
 * 补拍：两张被指纹去重误吞的关键证据。
 *   1) 在急症筛查题上说「对了…」被判成「有危险信号」的屏幕；
 *   2) 断网后「重新连接」失败的提示。
 * 只在需要补图时运行：node capture-missing.mjs
 */
import { chromium } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ROOT, startServer } from "./lib/server.mjs";
import { choose, say, startSession, waitStable } from "./lib/drive.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(HERE, "screenshots");
const BASE_LABEL = "补拍";

let seq = 200;
const shot = async (page, slug) => {
  const name = `r3-${(seq += 1)}-${BASE_LABEL}-${slug}.png`;
  await page.screenshot({ path: path.join(SHOTS, name), fullPage: false });
  console.log("shot", name);
  return name;
};

const noop = {
  record: (entry) => console.log("·", entry.action, entry.detail ?? ""),
};

const server = await startServer({
  port: 19031,
  dataDir: path.join(ROOT, "output", "r3-patient-data"),
});
const browser = await chromium.launch({ channel: "chrome" });
try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    locale: "zh-CN",
  });
  const page = await context.newPage();
  const sessionId = await startSession(
    page,
    noop,
    BASE_LABEL,
    "Z",
    server.base,
  );
  await choose(page, noop, sessionId, BASE_LABEL, "Z", "孩子");
  await choose(page, noop, sessionId, BASE_LABEL, "Z", "女");
  await say(page, noop, sessionId, BASE_LABEL, "Z", "4岁");
  await say(page, noop, sessionId, BASE_LABEL, "Z", "孩子咳嗽三天了");
  await say(
    page,
    noop,
    sessionId,
    BASE_LABEL,
    "Z",
    "对了，帮我儿子也看一下，他也咳嗽",
  );
  await waitStable(page, 400);
  await shot(page, "F2-yesword-emergency");

  // 断网后的错误条与重连失败提示
  await page.route("**/api/**", (route) => route.abort());
  await page.reload();
  await waitStable(page, 1200);
  await shot(page, "M1-offline-alert");
  await page
    .locator("#connection-retry")
    .click()
    .catch(() => {});
  await waitStable(page, 1500);
  await shot(page, "M2-retry-failed");
  await page.unroute("**/api/**");
  await context.close();
} finally {
  await browser.close();
  await server.stop();
}
