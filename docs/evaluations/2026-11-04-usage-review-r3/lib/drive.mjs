/**
 * 驱动层：像患者一样点、说、等。
 *
 * 等待策略换掉了上一轮的固定 sleep：每次动作前后比对 snapshot 里
 * `turn.agent_reply` 的条数，涨了才算这一轮结束——比"等发送键变可见"
 * 可靠（任务卡、急症卡、随访卡都不走发送键），也快得多。
 */
import { fingerprintOf } from "./record.mjs";

const ARROW = /[↗→›»\s]+$/u;
export const normalize = (text) =>
  (text ?? "").replace(ARROW, "").replace(/\s+/g, " ").trim();

export async function waitStable(page, settle = 120) {
  await page
    .locator("#busy-indicator")
    .waitFor({ state: "hidden", timeout: 20000 })
    .catch(() => {});
  await page.evaluate(
    () =>
      new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
  );
  await page.waitForTimeout(settle);
}

export async function snapshot(page, sessionId) {
  return page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
    sessionId,
  );
}

export async function replyCount(page, sessionId) {
  const snap = await snapshot(page, sessionId);
  return (snap.events ?? []).filter((e) => e.event_type === "turn.agent_reply")
    .length;
}

export async function lastReply(page, sessionId) {
  const snap = await snapshot(page, sessionId);
  const replies = (snap.events ?? []).filter(
    (e) => e.event_type === "turn.agent_reply",
  );
  return replies.at(-1)?.payload?.reply ?? null;
}

export async function stateOf(page, sessionId) {
  const snap = await snapshot(page, sessionId);
  return snap.state ?? {};
}

/** 等到下一轮回复产生；超时抛错（说明这一步根本没被受理）。 */
export async function waitTurn(page, sessionId, before, timeout = 25000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if ((await replyCount(page, sessionId)) > before) {
      await waitStable(page);
      return true;
    }
    await page.waitForTimeout(150);
  }
  throw new Error(`等待回复超时（${timeout}ms）：本轮没有被受理`);
}

/** 当前屏的身份，用于截图去重与证据归档。 */
export async function screenMeta(page, sessionId, base) {
  const dom = await page.evaluate(() => {
    const text = (selector) =>
      document.querySelector(selector)?.textContent?.trim() ?? "";
    const visible = [
      ...document.querySelectorAll(
        "#active-question button, #active-task button, #quick-replies button",
      ),
    ]
      .filter((el) => el.offsetParent !== null)
      .map((el) => el.textContent?.trim() ?? "");
    return {
      phase: text("#phase-label"),
      title:
        text("#active-question h2") ||
        text("#active-task h2") ||
        text("#active-question h3"),
      labels: visible,
      bodyClass: document.body.className,
      dialogId: document.querySelector("dialog[open]")?.id ?? "",
    };
  });
  let questionId = null;
  let revision = "";
  if (sessionId) {
    try {
      const snap = await snapshot(page, sessionId);
      const replies = (snap.events ?? []).filter(
        (e) => e.event_type === "turn.agent_reply",
      );
      const reply = replies.at(-1)?.payload?.reply ?? {};
      questionId = reply.question_id ?? reply.kind ?? null;
      revision = String(
        snap.state?.revision ?? snap.state?.fact_revision ?? "",
      );
    } catch {
      /* 会话还没建好 */
    }
  }
  const meta = { ...base, ...dom, questionId, revision };
  meta.fp = fingerprintOf(meta);
  return meta;
}

/** 按文案找一个可见按钮并点击；先精确、再前缀、再包含，优先焦点容器内。 */
export async function clickButton(page, label, { timeout = 15000 } = {}) {
  const wanted = normalize(label);
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const buttons = page.locator("button:visible");
    const total = await buttons.count();
    let best = null;
    for (let index = 0; index < total; index += 1) {
      const button = buttons.nth(index);
      const text = normalize(await button.textContent().catch(() => ""));
      const aria = normalize(
        await button.getAttribute("aria-label").catch(() => ""),
      );
      let score = 0;
      if (text === wanted || aria === wanted) score = 3;
      else if (text.startsWith(wanted) || aria.startsWith(wanted)) score = 2;
      else if (
        wanted.length >= 2 &&
        (text.includes(wanted) || aria.includes(wanted))
      )
        score = 1;
      if (!score) continue;
      const inFocus = await button
        .evaluate((el) =>
          Boolean(
            el.closest(
              "#active-question, #active-task, #active-observation, #quick-replies, dialog",
            ),
          ),
        )
        .catch(() => false);
      if (inFocus) score += 0.5;
      if (!best || score > best.score) best = { score, index };
    }
    if (best && best.score > 0) {
      await buttons.nth(best.index).click({ timeout: 8000 });
      return true;
    }
    await page.waitForTimeout(200);
  }
  return false;
}

/** 点一个选项，并等这一轮回复落地。 */
export async function choose(
  page,
  rec,
  sessionId,
  persona,
  group,
  label,
  turn,
) {
  const before = await replyCount(page, sessionId);
  const clicked = await clickButton(page, label);
  if (!clicked) {
    rec.record({
      group,
      persona,
      action: "click-miss",
      detail: `没找到按钮「${label}」`,
    });
    return false;
  }
  await waitTurn(page, sessionId, before);
  rec.record({
    group,
    persona,
    action: `选择「${label}」`,
    turn: turn ?? before + 1,
  });
  return true;
}

/** 在输入框里说一句话，并等这一轮回复落地。 */
export async function say(page, rec, sessionId, persona, group, text, turn) {
  const before = await replyCount(page, sessionId);
  const composer = page.locator("#message-input");
  const taskInput = page.locator("#active-task textarea:visible").first();
  const useTask =
    !(await composer.isVisible().catch(() => false)) &&
    (await taskInput.isVisible().catch(() => false));
  if (useTask) {
    await taskInput.fill(text);
  } else {
    await composer.fill(text);
  }
  await page.locator("#send-button").click({ timeout: 8000 });
  await waitTurn(page, sessionId, before);
  rec.record({
    group,
    persona,
    action: `说「${text}」`,
    turn: turn ?? before + 1,
  });
  return true;
}

/** 打开一次新会话，返回 session_id。 */
export async function startSession(page, rec, persona, group, base) {
  await page.goto(`${base}/`);
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
    { timeout: 20000 },
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const { session_id: sessionId } = await (await created).json();
  await waitStable(page);
  rec.record({
    group,
    persona,
    action: "开始问诊",
    sessionId,
    detail: sessionId,
  });
  return sessionId;
}

/** 侧栏切视图。 */
export async function gotoView(page, view) {
  await page.locator(`[data-view="${view}"]`).click();
  await waitStable(page, 250);
}

/** 走完整个"提问 → 回答"循环，直到谓词成立或步数用尽。 */
export async function answerUntil(page, rec, sessionId, persona, group, opts) {
  const {
    answers = {},
    choices = {},
    done,
    max = 20,
    onStep,
    shoot = true,
  } = opts;
  let lastShot = null;
  for (let step = 0; step < max; step += 1) {
    if (done && (await done())) return "done";
    const reply = await lastReply(page, sessionId);
    if (!reply) return "no-reply";
    if (onStep && (await onStep(reply, step))) continue;
    const questionId = reply.question_id;
    if (!questionId) return "no-question";
    if (shoot && questionId !== lastShot) {
      lastShot = questionId;
      await shotHere(
        page,
        rec,
        sessionId,
        { persona, group },
        `q-${questionId}`,
      );
    }
    if (questionId.startsWith("warning_")) {
      await choose(page, rec, sessionId, persona, group, "没有");
      continue;
    }
    if (choices[questionId] !== undefined) {
      await choose(page, rec, sessionId, persona, group, choices[questionId]);
      continue;
    }
    if (answers[questionId] !== undefined) {
      const value =
        typeof answers[questionId] === "function"
          ? answers[questionId](reply)
          : answers[questionId];
      await say(page, rec, sessionId, persona, group, value);
      continue;
    }
    throw new Error(
      `画像缺问法答案：${questionId}（屏幕：${await page
        .locator("#active-question h2")
        .textContent()
        .catch(() => "?")}）`,
    );
  }
  return "max-steps";
}

/** 截一张当前屏（自动补齐 meta）。 */
export async function shotHere(page, rec, sessionId, base, slug, options) {
  const meta = await screenMeta(page, sessionId, base);
  return rec.shot(page, meta, slug, options);
}
