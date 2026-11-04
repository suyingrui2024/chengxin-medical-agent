/**
 * 使用体验走查·第二轮：覆盖第一轮未触达的区域。
 *   会谈记录/随访 · 健康档案 · 报告粘贴→核对→入档 · 就诊交接摘要
 *   设置四分区（外观切换） · 执行工作台不可用态 · 窄屏 390px
 * 证据：r2-NN-*.png + steps-r2.jsonl（与第一轮同一目录）。
 */
import { chromium } from "@playwright/test";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(HERE, "screenshots");
const LOG = path.join(HERE, "steps-r2.jsonl");
const BASE = "http://127.0.0.1:19011";
mkdirSync(SHOTS, { recursive: true });
writeFileSync(LOG, "");

let seq = 0;
function record(action, detail = {}) {
  const entry = { seq: ++seq, round: 2, action, ...detail };
  appendFileSync(LOG, JSON.stringify(entry) + "\n", "utf-8");
  console.log(`[R2-${String(entry.seq).padStart(2, "0")}] ${action}`);
}

async function shot(page, name) {
  const file = path.join(SHOTS, `r2-${String(++seq).padStart(2, "0")}-${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  record(`截图 ${path.basename(file)}`);
}

async function gotoView(page, view) {
  await page.locator(`[data-view="${view}"]`).click();
  await page.waitForTimeout(900);
  record(`切到视图 ${view}`);
}

async function answer(page, text) {
  await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  await page.getByRole("button", { name: "发送回答" }).click();
  await page.locator("#send-button").waitFor({ state: "visible", timeout: 10000 });
}

async function choose(page, label) {
  await page
    .getByRole("button", { name: label, exact: false })
    .first()
    .click({ timeout: 10000 });
  await page.locator("#send-button").waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  record(`选择「${label}」`);
}

const browser = await chromium.launch({ channel: "chrome" });

try {
  // =========================================================================
  // A. 完整问诊到确认 → 交接摘要 → 报告粘贴 → 入档
  // =========================================================================
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(BASE + "/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await choose(page, "我自己");
  await choose(page, "男");
  await answer(page, "32岁");
  await answer(page, "最近两周偶尔心慌，睡不好");
  // 逐题回答直到摘要：warning 选"没有"，其余按映射
  const ANSWERS = {
    onset: "两周前开始",
    severity: "2分",
    associated_symptoms: "没有其他不适",
    resp_breathing: "呼吸正常",
    resp_cough_details: "没有咳嗽",
    resp_intake: "能正常吃喝",
    head_details: "没有",
    gi_details: "没有",
    dizzy_details: "没有",
    back_details: "没有",
    urinary_details: "没有",
    skin_details: "没有",
    reported_temperature: "没量过",
    medications_taken: "没有",
    allergies: "没有",
    relevant_history: "没有",
    patient_goal: "整理信息",
  };
  const CHOICES = { emergency_screen: "都没有", trajectory: "变化不大" };
  for (let i = 0; i < 24; i += 1) {
    if (await page.getByText("确认后会结束本次问诊").first().isVisible().catch(() => false)) break;
    const snap = await page.evaluate(async () => {
      const id = location.pathname; // fallback below
      return null;
    });
    // 从事件流读当前问题
    const qid = await page.evaluate(async () => {
      const list = await (await fetch("/api/sessions")).json();
      const id = list.sessions[0]?.session_id;
      if (!id) return null;
      const snap = await (await fetch(`/api/sessions/${id}/snapshot`)).json();
      const replies = snap.events.filter((e) => e.event_type === "turn.agent_reply");
      return replies.at(-1)?.payload?.reply?.question_id ?? null;
    });
    if (!qid) break;
    if (qid.startsWith("warning_")) { await choose(page, "没有"); continue; }
    if (qid === "symptom_location" || qid === "body_location") { await choose(page, "头/面部"); continue; }
    if (CHOICES[qid]) { await choose(page, CHOICES[qid]); continue; }
    if (ANSWERS[qid]) { await answer(page, ANSWERS[qid]); continue; }
    record(`未知题 ${qid}，截屏后跳过`);
    await shot(page, `unknown-${qid}`);
    break;
  }
  // 摘要确认
  const confirm = page.getByRole("button", { name: /确认摘要/ }).first();
  if (await confirm.isVisible().catch(() => false)) {
    await confirm.click();
    await page.waitForTimeout(1500);
    record("摘要已确认");
  }
  await shot(page, "conclusion");

  // 交接摘要
  const handoffBtn = page.locator("#open-handoff");
  if (await handoffBtn.isVisible().catch(() => false)) {
    await handoffBtn.click();
    await page.waitForTimeout(1200);
    await shot(page, "handoff");
    const printBtn = await page.locator("#handoff-print, [id*=print]").count();
    record("交接摘要对话框", { print_buttons: printBtn });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  } else {
    record("交接摘要入口不可见", { note: "确认后按钮仍隐藏？" });
    await shot(page, "handoff-missing");
  }

  // 报告粘贴 → 提取 → 核对
  const reportBtn = page.locator("#open-report-draft");
  if (await reportBtn.isVisible().catch(() => false)) {
    await reportBtn.click();
    await page.waitForTimeout(800);
    await page
      .locator("#report-text")
      .fill(
        "检验报告日期 2026-11-01\n空腹血糖 5.6 mmol/L（参考 3.9-6.1）\n总胆固醇 6.2 mmol/L（参考 <5.2）\n血压 128/84 mmHg",
      );
    await shot(page, "report-dialog");
    await page.getByRole("button", { name: "提取待核对项目" }).click();
    await page.waitForTimeout(1500);
    await shot(page, "report-draft-result");
    record("报告提取完成", { feedback: await page.locator("#report-feedback").textContent().catch(() => null) });
    // 逐项确认（按钮文本待运行时观察）
    const itemButtons = await page
      .locator("#report-draft-result button:visible")
      .allTextContents()
      .catch(() => []);
    record("核对区按钮", { choices: itemButtons.slice(0, 8) });
    for (const label of itemButtons) {
      if (/确认|加入/.test(label)) {
        await page.locator("#report-draft-result button:visible", { hasText: label }).first().click();
        await page.waitForTimeout(700);
      }
    }
    await shot(page, "report-confirmed");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  } else {
    record("报告粘贴入口不可见");
  }

  // 健康档案：报告确认后应有记录
  await gotoView(page, "records");
  await shot(page, "records-after-report");
  const recordsCount = await page.locator("#records-count").textContent().catch(() => "?");
  record("健康档案计数", { count: recordsCount });

  // 会谈记录 + 随访
  await gotoView(page, "history");
  await shot(page, "history-view");
  const followupDue = await page.locator("#followup-due-count").textContent().catch(() => "?");
  record("随访到期计数", { count: followupDue });

  // 设置四分区
  await gotoView(page, "settings");
  await shot(page, "settings-model");
  // 外观切换
  const themeBtn = page.getByRole("button", { name: /夜航/ }).first();
  if (await themeBtn.isVisible().catch(() => false)) {
    await themeBtn.click();
    await page.waitForTimeout(600);
    await shot(page, "settings-night");
    record("外观切换到夜航");
    await page.getByRole("button", { name: /知微绿/ }).first().click().catch(() => {});
    await page.waitForTimeout(400);
  } else {
    record("外观主题按钮未见");
  }
  // 数据分区（用药提醒）
  const dataTab = page.getByText(/数据/, { exact: false }).first();
  if (await dataTab.isVisible().catch(() => false)) {
    await dataTab.click().catch(() => {});
    await page.waitForTimeout(600);
    await shot(page, "settings-data");
  }
  await gotoView(page, "execution");
  await shot(page, "execution-unavailable");
  record("执行工作台状态", {
    text: (await page.locator("#view-execution").textContent().catch(() => "")).slice(0, 200),
  });
  await page.close();

  // =========================================================================
  // B. 窄屏 390px：问诊与档案
  // =========================================================================
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await mobile.goto(BASE + "/");
  await mobile.waitForTimeout(1500);
  await mobile.getByRole("button", { name: "开始问诊", exact: true }).click().catch(() => {});
  await mobile.waitForTimeout(1000);
  await shot(mobile, "mobile-consultation");
  await gotoView(mobile, "records");
  await shot(mobile, "mobile-records");
  await mobile.close();

  console.log("round-2 walkthrough complete");
} finally {
  await browser.close();
}
