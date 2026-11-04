/**
 * 使用体验走查驱动：像患者一样点完 demo 引擎的全流程，每一步截图 + 记录。
 *
 * 三个画像各开独立会话；坏路径走 API。产物：
 *   screenshots/NN-<persona>-<step>.png   每个环节的画面
 *   steps.jsonl                           每步的动作与当时屏幕上的关键文本
 *
 * 运行：node walkthrough.mjs（需要 demo server 已在 19011 端口）
 */
import { chromium } from "@playwright/test";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(HERE, "screenshots");
const LOG = path.join(HERE, "steps.jsonl");
const BASE = "http://127.0.0.1:19011";
mkdirSync(SHOTS, { recursive: true });
writeFileSync(LOG, "");

let seq = 0;
function record(persona, action, detail = {}) {
  const entry = { seq: ++seq, persona, action, ...detail };
  appendFileSync(LOG, JSON.stringify(entry) + "\n", "utf-8");
  console.log(`[${String(entry.seq).padStart(2, "0")}] ${persona}: ${action}`);
}

async function shot(page, persona, name) {
  const file = path.join(SHOTS, `${String(++seq).padStart(2, "0")}-${persona}-${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  record(persona, `截图 ${path.basename(file)}`, {
    question: await onScreenQuestion(page),
    phase: await safeText(page, "#phase-label"),
  });
}

async function safeText(page, selector) {
  try {
    return (await page.locator(selector).textContent({ timeout: 2000 }))?.trim();
  } catch {
    return null;
  }
}

/** 当前待回答的问题：标题 + 可点选项 */
async function onScreenQuestion(page) {
  const title = await safeText(page, "#active-question h2");
  let choices = [];
  try {
    choices = await page
      .locator("#active-question button:visible")
      .allTextContents({ timeout: 2000 });
  } catch {
    /* 文本输入题没有选项 */
  }
  return { title, choices: choices.slice(0, 8) };
}

async function newPage(browser) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  return page;
}

async function startSession(page, persona) {
  await page.goto(BASE + "/");
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const { session_id: id } = await (await created).json();
  record(persona, "开始问诊", { session_id: id, question: await onScreenQuestion(page) });
  return id;
}

async function clickChoice(page, persona, label) {
  // 可访问名可能带 ↗ 装饰符；warning_ 题的按钮也可能不在 active-question
  // 容器里——用页面级 role + 子串匹配，两种情况都不受影响。
  const btn = page.getByRole("button", { name: label, exact: false }).first();
  await btn.click({ timeout: 10000 });
  await page
    .locator("#send-button")
    .waitFor({ state: "visible", timeout: 5000 })
    .catch(() => {});
  record(persona, `选择「${label}」`, { question: await onScreenQuestion(page) });
}

async function answer(page, persona, text) {
  await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  await page.getByRole("button", { name: "发送回答" }).click();
  await page
    .locator("#send-button")
    .waitFor({ state: "visible", timeout: 10000 });
  record(persona, `回答「${text}」`, { question: await onScreenQuestion(page) });
}

async function questionId(page, id) {
  const snapshot = await page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
    id,
  );
  const replies = snapshot.events.filter((e) => e.event_type === "turn.agent_reply");
  return replies.at(-1)?.payload?.reply?.question_id ?? null;
}

/** 逐题回答直到 isDone()；warning_* 一律选「没有」。 */
async function drive(page, persona, id, { answers, choices = {}, isDone, max = 18, shotEvery = 2 }) {
  const seen = new Set();
  for (let step = 0; step < max; step += 1) {
    if (await isDone(page)) return "done";
    if (await handleLocationTask(page, persona)) continue;
    const q = await questionId(page, id);
    if (!q) return "no-question";
    const { title } = await onScreenQuestion(page);
    if (title && !seen.has(title)) {
      seen.add(title);
      await shot(page, persona, `q-${q.replace(/[^a-z0-9_]/gi, "")}`);
    } else if (step % shotEvery === 0) {
      await shot(page, persona, `step-${step}`);
    }
    if (q.startsWith("warning_")) {
      await clickChoice(page, persona, "没有");
      continue;
    }
    if (choices[q]) {
      await clickChoice(page, persona, choices[q]);
      continue;
    }
    const text = answers[q];
    if (text === undefined) throw new Error(`画像缺问法答案: ${q}（屏幕：${title}）`);
    await answer(page, persona, text);
  }
  return "max-steps";
}

const summaryDone = (page) =>
  page
    .getByText("确认后会结束本次问诊", { exact: false })
    .first()
    .isVisible()
    .catch(() => false);

/** 图示定位任务：选部位 → 记录（确认按钮即所见所记），每步截图。 */
async function handleLocationTask(page, persona) {
  const card = page.getByText("指出最明显的不适部位", { exact: false }).first();
  if (!(await card.isVisible().catch(() => false))) return false;
  record(persona, "【图示定位任务出现】");
  await shot(page, persona, "location-part");
  const part = page.getByRole("button", { name: "头/面部", exact: true }).first();
  if (await part.isVisible().catch(() => false)) {
    await part.click();
    await page.waitForTimeout(400);
    record(persona, "定位选择「头/面部」");
  }
  const recordBtn = page
    .getByRole("button", { name: /^记录：/ })
    .first();
  if (await recordBtn.isVisible().catch(() => false)) {
    const label = await recordBtn.textContent();
    await recordBtn.click();
    await page.waitForTimeout(800);
    await shot(page, persona, "location-recorded");
    record(persona, `定位提交「${label?.trim()}」`, {
      question: await onScreenQuestion(page),
    });
  } else {
    record(persona, "定位记录按钮未见");
  }
  return true;
}

// ---------------------------------------------------------------------------
// 画像一：自护的头痛患者（蓝）
// ---------------------------------------------------------------------------
const BLUE_ANSWERS = {
  onset: "两天前逐渐出现",
  severity: "3分",
  associated_symptoms: "没有其他不适",
  head_details: "后脑勺胀痛，一阵一阵的",
  resp_breathing: "呼吸正常",
  resp_cough_details: "没有咳嗽",
  resp_intake: "能正常喝水吃饭",
  gi_details: "没有",
  dizzy_details: "没有",
  back_details: "没有",
  urinary_details: "没有",
  skin_details: "没有",
  reported_temperature: "没量过",
  medications_taken: "没有吃过药",
  allergies: "没有",
  relevant_history: "没有",
  patient_goal: "看看要不要去医院",
  age_text: "32岁",
  age: "32岁",
  chief: "后脑勺胀痛两天",
};

async function personaBlue(browser) {
  const P = "蓝-自护";
  const page = await newPage(browser);
  const id = await startSession(page, P);
  await shot(page, P, "opening");
  const choices = await page.locator("#active-question button:visible").allTextContents();
  record(P, "开场选项", { choices });
  await clickChoice(page, P, "我自己");
  await clickChoice(page, P, "男");
  await answer(page, P, "32岁");
  await answer(page, P, "后脑勺胀痛两天，一阵一阵的");
  await shot(page, P, "safety-screen");
  await drive(page, P, id, {
    answers: BLUE_ANSWERS,
    choices: { trajectory: "变化不大", emergency_screen: "都没有" },
    isDone: summaryDone,
  });
  await shot(page, P, "summary-sheet");
  // 摘要确认按钮长什么样
  const buttons = await page
    .locator("#active-question button:visible, .summary button:visible")
    .allTextContents()
    .catch(() => []);
  record(P, "摘要页可用按钮", { choices: buttons.slice(0, 8) });
  const confirm = page.getByRole("button", { name: /确认/ }).first();
  if (await confirm.isVisible().catch(() => false)) {
    await confirm.click();
    await page.waitForTimeout(1200);
    await shot(page, P, "after-confirm");
  }
  await page.close();
}

// ---------------------------------------------------------------------------
// 画像二：胸痛急症（红）
// ---------------------------------------------------------------------------
async function personaRed(browser) {
  const P = "红-急症";
  const page = await newPage(browser);
  const id = await startSession(page, P);
  await clickChoice(page, P, "我自己");
  await clickChoice(page, P, "男");
  await answer(page, P, "58岁");
  await answer(page, P, "胸口像压了块大石头，喘不上气，一直出冷汗");
  await shot(page, P, "after-chief");
  await drive(page, P, id, {
    answers: {
      ...BLUE_ANSWERS,
      chief: "胸口压榨样疼痛",
      onset: "一小时前突然开始",
      severity: "9分",
      age: "58岁",
      head_details: "没有",
      gi_details: "没有",
    },
    choices: {
      trajectory: "持续加重",
      emergency_screen: "有任何一项",
      "warning_chest_pain": "有",
    },
    isDone: (page) =>
      page
        .locator("#messages .emergency, .emergency, text=120")
        .first()
        .isVisible()
        .catch(() => false),
    max: 10,
  });
  await shot(page, P, "escalation");
  record(P, "急症路径结束状态", { phase: await safeText(page, "#phase-label") });
  await page.close();
}

// ---------------------------------------------------------------------------
// 画像三：帮孩子问的家长（黄）
// ---------------------------------------------------------------------------
const YELLOW_ANSWERS = {
  ...BLUE_ANSWERS,
  age: "3岁",
  chief: "孩子发烧38度5",
  reported_temperature: "38.5℃",
  head_details: "没有",
};

async function personaYellow(browser) {
  const P = "黄-家长";
  const page = await newPage(browser);
  const id = await startSession(page, P);
  await shot(page, P, "opening");
  const labels = await page.locator("#active-question button:visible").allTextContents();
  record(P, "开场选项", { choices: labels });
  await clickChoice(page, P, "孩子");
  await page.waitForTimeout(600);
  const follow = await onScreenQuestion(page);
  record(P, "代答入口之后", { question: follow });
  await shot(page, P, "subject");
  // 孩子的性别题
  await clickChoice(page, P, "男");
  await answer(page, P, "3岁");
  await answer(page, P, "孩子发烧38度5，精神不太好");
  await shot(page, P, "after-chief");
  await drive(page, P, id, {
    answers: YELLOW_ANSWERS,
    choices: { trajectory: "变化不大", emergency_screen: "都没有" },
    isDone: summaryDone,
    max: 12,
  });
  await shot(page, P, "end-state");
  await page.close();
}

// ---------------------------------------------------------------------------
// 坏路径（API 层 + 页面双击）
// ---------------------------------------------------------------------------
async function badPaths(browser) {
  const P = "坏路径";
  const page = await newPage(browser);
  // F3 生效后，goto 会自动回到上一个画像未结束的会话——直接复用它。
  await page.goto(BASE + "/");
  await page.waitForTimeout(2000);
  const list = await page.evaluate(async () =>
    (await fetch("/api/sessions")).json(),
  );
  const id = list.sessions[0]?.session_id;
  record(P, "复用活动会话", { session_id: id });

  // 1. 空消息
  const empty = await page.evaluate(async (id) => {
    const r = await fetch(`/api/sessions/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "" }),
    });
    return { status: r.status, body: (await r.text()).slice(0, 200) };
  }, id);
  record(P, "空消息", empty);

  // 2. 超长文本（5000 字）
  const long = await page.evaluate(async (id) => {
    const r = await fetch(`/api/sessions/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "头痛".repeat(2500) }),
    });
    return { status: r.status, body: (await r.text()).slice(0, 200) };
  }, id);
  record(P, "5000字消息", long);

  // 3. 不存在的会话
  const missing = await page.evaluate(async () => {
    const r = await fetch(`/api/sessions/does-not-exist/snapshot`);
    return { status: r.status, body: (await r.text()).slice(0, 160) };
  });
  record(P, "不存在的会话", missing);

  // 4. 发送按钮双击（连点两次）——仅当自由输入框在屏时执行
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  if (await input.isVisible().catch(() => false)) {
    await input.fill("后脑勺胀痛");
    await page.getByRole("button", { name: "发送回答" }).click();
    await page
      .getByRole("button", { name: "发送回答" })
      .click({ force: true, timeout: 1500 })
      .catch(() => {});
    await page.waitForTimeout(800);
    const dup = await page.locator(".message.user").count();
    record(P, "双击发送", { user_messages: dup });
    await shot(page, P, "double-click");
  } else {
    record(P, "双击发送：会话停在任务卡，输入框不在屏，跳过");
  }

  // 5. 中途刷新，看恢复
  await page.reload();
  await page.waitForTimeout(1500);
  await shot(page, P, "after-reload");
  record(P, "刷新恢复", {
    phase: await safeText(page, "#phase-label"),
    question: await onScreenQuestion(page),
  });
  await page.close();
}

const browser = await chromium.launch({ channel: "chrome" });
try {
  await personaBlue(browser);
  await personaRed(browser);
  await personaYellow(browser);
  await badPaths(browser);
  console.log("walkthrough complete");
} finally {
  await browser.close();
}
