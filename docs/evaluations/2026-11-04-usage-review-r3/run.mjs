/**
 * 第三轮用户视角走查 · 入口。
 *
 *   node run.mjs --fresh --groups=A,B
 *   node run.mjs --groups=C,D,E
 *
 * 一条命令内：清场 → 起 demo 服务 → 逐组驱动浏览器 → 写对话全文 → 收服务。
 * 不依赖任何预先在跑的服务（上一轮就是因为复用了别人的服务而读错会话）。
 */
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ROOT, startServer } from "./lib/server.mjs";
import { createRecorder, writeDialogue } from "./lib/record.mjs";
import { groups as coreGroups } from "./flows/core.mjs";
import { groups as structuredGroups } from "./flows/structured.mjs";
import { groups as peripheryGroups } from "./flows/periphery.mjs";
import { groups as surfaceGroups } from "./flows/surface.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.join(ROOT, "output", "r3-patient-data");

const args = Object.fromEntries(
  process.argv.slice(2).map((arg) => {
    const [key, value = "true"] = arg.replace(/^--/, "").split("=");
    return [key, value];
  }),
);

const GROUPS = {
  ...coreGroups,
  ...structuredGroups,
  ...peripheryGroups,
  ...surfaceGroups,
};
const wanted = (args.groups ?? Object.keys(GROUPS).join(","))
  .split(",")
  .map((g) => g.trim())
  .filter(Boolean);

mkdirSync(DATA_DIR, { recursive: true });

function makeContext({ browser, base, rec }) {
  const tracked = [];
  return {
    browser,
    base,
    rec,
    dir: HERE,
    track(persona, sessionId) {
      if (sessionId && !tracked.some((item) => item.sessionId === sessionId)) {
        tracked.push({ persona, sessionId });
      }
    },
    async page(viewport = { width: 1280, height: 900 }) {
      const context = await browser.newContext({ viewport, locale: "zh-CN" });
      await context.addInitScript(() => {
        try {
          localStorage.removeItem("medagent:last-session-id");
        } catch {
          /* 隐私模式 */
        }
      });
      return context.newPage();
    },
    async close(page) {
      await page
        .context()
        .close()
        .catch(() => {});
    },
    /** 组结束时把每个会话写成逐轮对话全文（同画像多会话分开编号）。 */
    async flush() {
      const counts = new Map();
      for (const item of tracked) {
        try {
          const response = await fetch(
            `${base}/api/sessions/${item.sessionId}/snapshot`,
          );
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const snapshot = await response.json();
          const nth = (counts.get(item.persona) ?? 0) + 1;
          counts.set(item.persona, nth);
          const file = writeDialogue({
            dir: HERE,
            persona: item.persona,
            label: nth === 1 ? item.persona : `${item.persona}-${nth}`,
            sessionId: item.sessionId,
            snapshot,
            shots: rec.shotsFor(item.persona),
          });
          rec.record({ action: "dialogue", detail: path.basename(file) });
        } catch (error) {
          rec.record({
            action: "dialogue-failed",
            detail: `${item.sessionId}: ${error}`,
          });
        }
      }
    },
  };
}

const server = await startServer({
  port: Number(args.port ?? 19021),
  dataDir: DATA_DIR,
});
const rec = createRecorder({ dir: HERE, fresh: args.fresh === "true" });
const browser = await chromium.launch({ channel: "chrome" });
const started = Date.now();
let failed = null;

try {
  for (const name of wanted) {
    const flow = GROUPS[name];
    if (!flow)
      throw new Error(
        `未知的组：${name}（可用：${Object.keys(GROUPS).join(",")}）`,
      );
    rec.record({ group: name, action: "group-start" });
    const ctx = makeContext({ browser, base: server.base, rec });
    try {
      await flow(ctx);
    } catch (error) {
      rec.record({
        group: name,
        action: "group-error",
        detail: String(error).slice(0, 400),
      });
      failed = error;
    } finally {
      await ctx.flush();
      rec.record({ group: name, action: "group-done" });
    }
    if (failed) break;
  }
} finally {
  await browser.close().catch(() => {});
  await server.stop();
  const rows = rec.saveFindings();
  const stats = rec.stats();
  const summary = {
    groups: wanted,
    seconds: Math.round((Date.now() - started) / 1000),
    ...stats,
    findings: rows.length,
    failing: rows.filter((row) => !row.pass).length,
    serverTail: server.output().split("\n").slice(-6).join("\n"),
  };
  writeFileSync(
    path.join(HERE, "run-summary.json"),
    `${JSON.stringify(summary, null, 1)}\n`,
    "utf-8",
  );
  console.log("\n=== 汇总 ===");
  console.log(JSON.stringify(summary, null, 1));
  for (const row of rows.filter((item) => !item.pass)) {
    console.log(
      `FAIL ${row.id} [${row.severity}] ${row.expected} → ${JSON.stringify(row.actual).slice(0, 160)}`,
    );
  }
}

if (failed) {
  console.error("\n组执行失败：", failed.message);
  process.exitCode = 1;
}
