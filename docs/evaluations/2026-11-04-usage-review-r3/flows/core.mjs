/**
 * A 连续多轮主流程 + B 两条升级路径。
 *
 * 这里刻意不做"每步断言功能对不对"——功能有 52 个 spec 在守。这里要看的
 * 是患者连着用下去会撞到什么：更正之后旧答案去哪了、疼到 8 分为何还问、
 * 关了会话再回来还认不认得。
 */
import {
  answerUntil,
  clickButton,
  lastReply,
  replyCount,
  say,
  choose,
  shotHere,
  startSession,
  stateOf,
  waitStable,
  waitTurn,
} from "../lib/drive.mjs";
import {
  check,
  countVisible,
  noInternalTokens,
  noOverflow,
} from "../lib/checks.mjs";

const BLUE = "蓝-自护";
const RED = "红-急症";
const AMBER = "黄-家长";

/** 后脑勺胀痛这条通路上，患者会怎么说话。 */
const BLUE_ANSWERS = {
  onset: "两天前开始的，一开始只是有点胀",
  associated_symptoms: "没有恶心，也没发烧",
  head_details: "后脑勺这边发胀，低头的时候更明显",
  medications_taken: "这两天没吃药",
  allergies: "没有过敏",
  relevant_history: "以前偶尔也这样，没有高血压",
  patient_goal: "想知道要不要去医院看看",
  reported_temperature: "36.8℃",
};
const BLUE_CHOICES = { trajectory: "一阵一阵的", emergency_screen: "都没有" };

const isVisible = async (page, selector) =>
  page
    .locator(selector)
    .first()
    .isVisible()
    .catch(() => false);

const conclusionVisible = (page) =>
  page
    .getByText("不替代医生面诊", { exact: false })
    .first()
    .isVisible()
    .catch(() => false);

/** 任务卡内部的点击：纯前端步骤，不会产生回合，不能等回复。 */
async function tapInTask(page, label) {
  const ok = await clickButton(page, label, { timeout: 6000 });
  if (ok) await waitStable(page, 180);
  return ok;
}

/** 0–10 分那题：点分数 → 点「确认 N 分」。 */
async function answerSeverity(page, ctx, sessionId, persona, group, score) {
  const before = await replyCount(page, sessionId);
  await clickButton(page, String(score));
  await waitStable(page, 150);
  await clickButton(page, "确认");
  await waitTurn(page, sessionId, before);
  ctx.rec.record({
    group,
    persona,
    action: `严重程度 ${score} 分`,
    turn: before + 1,
  });
}

/** 图示定位任务：这个通路上是两步（部位 → 记录），左右/范围只在需要时出现。 */
async function walkLocation(page, ctx, sessionId, persona, group, tag = "A4") {
  const base = { persona, group };
  await shotHere(page, ctx.rec, sessionId, base, `${tag}-location-part`);
  await tapInTask(page, "头/面部");
  await shotHere(page, ctx.rec, sessionId, base, `${tag}-location-region`);
  // 可选细化步骤：出现才走，不出现不算错（不同通路步数不同）。
  for (const label of ["左侧", "右侧", "一片范围", "一个点"]) {
    if (await clickButton(page, label, { timeout: 1200 })) {
      await waitStable(page, 150);
      await shotHere(
        page,
        ctx.rec,
        sessionId,
        base,
        `${tag}-location-${label}`,
      );
    }
  }

  const before = await replyCount(page, sessionId);
  let recorded = false;
  const buttons = await page
    .locator("#active-task button:visible")
    .allTextContents();
  const record = buttons.find((text) => text.trim().startsWith("记录"));
  if (record) {
    await clickButton(page, record.trim());
    recorded = true;
    await waitTurn(page, sessionId, before);
  }
  await shotHere(page, ctx.rec, sessionId, base, `${tag}-location-recorded`);
  return recorded;
}

/** 完成一次体温任务：走到最后一步 → 点「填写结果」→ 在输入框填读数并发送。 */
async function completeTemperatureTask(
  page,
  ctx,
  sessionId,
  persona,
  group,
  value,
) {
  const base = { persona, group };
  const before = await replyCount(page, sessionId);
  await shotHere(page, ctx.rec, sessionId, base, "temperature-task");
  for (let step = 0; step < 4; step += 1) {
    if (await clickButton(page, "填写结果", { timeout: 800 })) break;
    if (!(await tapInTask(page, "下一步"))) break;
  }
  await page.locator("#message-input").fill(value);
  await shotHere(page, ctx.rec, sessionId, base, "temperature-input");
  await page.locator("#send-button").click();
  await waitTurn(page, sessionId, before, 30000).catch(() => {});
  return true;
}

/** 一直答到摘要卡出现；任务卡与严重度题在 onStep 里消化。 */
async function toSummary(
  page,
  ctx,
  sessionId,
  persona,
  group,
  answers,
  choices,
) {
  return answerUntil(page, ctx.rec, sessionId, persona, group, {
    answers,
    choices,
    max: 24,
    done: async () => (await lastReply(page, sessionId))?.kind === "summary",
    onStep: async (reply) => {
      if (await isVisible(page, "#active-task")) {
        await walkLocation(page, ctx, sessionId, persona, group);
        return true;
      }
      if (reply.question_id === "severity") {
        await answerSeverity(page, ctx, sessionId, persona, group, 6);
        return true;
      }
      return false;
    },
  });
}

/** 在事实抽屉里更正一条记录。返回是否成功。 */
async function correctFact(
  page,
  ctx,
  sessionId,
  persona,
  group,
  labelText,
  newText,
) {
  const base = { persona, group };
  if (!(await isVisible(page, "#facts-panel"))) {
    await page.locator("#toggle-facts").click();
    await waitStable(page, 200);
  }
  await shotHere(
    page,
    ctx.rec,
    sessionId,
    base,
    group === "A" ? "A6-facts-drawer" : `${group}-facts`,
  );
  const row = page
    .locator("#fact-list .fact-row")
    .filter({ hasText: labelText })
    .first();
  const edit = row.locator("button.fact-edit").first();
  const before = await replyCount(page, sessionId);
  await row.scrollIntoViewIfNeeded().catch(() => {});
  await edit.scrollIntoViewIfNeeded().catch(() => {});
  // 抽屉内部有自己的滚动容器，Playwright 的自动滚动有时仍判定在视口外；
  // 这里直接派发一次 DOM 点击（患者用鼠标滚轮就能滚到，不算绕过真实交互）。
  await edit
    .evaluate((el) => el.click())
    .catch(async () => {
      await edit.click({ force: true });
    });
  await waitStable(page, 200);
  await page.locator("#edit-value").fill(newText);
  await shotHere(page, ctx.rec, sessionId, base, `${group}-edit-dialog`);
  await page
    .getByRole("button", { name: "保存并重新核对", exact: true })
    .click();
  await waitTurn(page, sessionId, before, 30000);
  ctx.rec.record({
    group,
    persona,
    action: `更正「${labelText}」→「${newText}」`,
    turn: before + 1,
  });
  if (await isVisible(page, "#facts-panel")) {
    await page
      .locator("#facts-close")
      .click()
      .catch(() => {});
    await waitStable(page, 150);
  }
  return true;
}

async function groupA(ctx) {
  const { base, rec } = ctx;
  const page = await ctx.page();
  try {
    const sessionId = await startSession(page, rec, BLUE, "A", base);
    ctx.track(BLUE, sessionId);

    // A1 开场
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A1-opening",
    );
    await check(page, rec, {
      id: "R3-A1-objects",
      severity: "P2",
      group: "A",
      persona: BLUE,
      expected: "开场给出三个咨询对象选项",
      run: () => countVisible(page, "#active-question button"),
    });
    await check(page, rec, {
      id: "R3-A1-composer",
      severity: "P3",
      group: "A",
      persona: BLUE,
      expected: "点选题下输入框仍可用于自由描述（不是缺陷，但要确认它有引导）",
      run: async () => ({
        pass: await page.locator("#message-input").isEditable(),
        actual: await page
          .locator("#composer-context")
          .innerText()
          .catch(() => null),
      }),
    });

    // A2 对象/性别/年龄
    await choose(page, rec, sessionId, BLUE, "A", "我自己");
    await choose(page, rec, sessionId, BLUE, "A", "男");
    await say(page, rec, sessionId, BLUE, "A", "32岁");
    await check(page, rec, {
      id: "R3-A2-age",
      severity: "P2",
      group: "A",
      persona: BLUE,
      expected: "年龄被记为已提供",
      run: async () => {
        const age = (await stateOf(page, sessionId)).facts?.age_years;
        return { pass: age?.assertion === "present", actual: age ?? null };
      },
    });

    // A3 主诉 → 安全筛查
    await say(page, rec, sessionId, BLUE, "A", "后脑勺胀痛两天了，一阵一阵的");
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A3-safety-screen",
    );
    await check(page, rec, {
      id: "R3-A3-signals",
      severity: "P1",
      group: "A",
      persona: BLUE,
      expected: "急症筛查六个信号与三个选项同时在屏、不需滚动即可读全",
      run: async () => {
        const text = await page.locator("#active-question").innerText();
        const signals = [
          "叫不醒",
          "抽搐",
          "嘴唇发紫",
          "呼吸困难",
          "胸痛",
          "出血",
        ].filter((word) => text.includes(word));
        return {
          pass: signals.length === 6,
          actual: { signals, viewport: await page.viewportSize() },
        };
      },
    });

    // A4 安全筛查否认 → 定位任务（出现时由 onStep 处理）
    await choose(page, rec, sessionId, BLUE, "A", "都没有");

    // A4b 进行中的问题上，主屏有没有更正入口？
    await page.locator("#toggle-facts").click();
    await waitStable(page, 200);
    await check(page, rec, {
      id: "R3-H2-edit-entry",
      severity: "P2",
      group: "A",
      persona: BLUE,
      expected:
        "进行中想改一句刚说的话时，问题卡/对话上没有入口，只能想到去开「已记录信息」抽屉",
      run: async () => {
        const inDrawer = await page
          .locator("#fact-list button.fact-edit")
          .count();
        const onMain = await page
          .locator(
            "#messages button, #active-question button, #active-task button",
          )
          .filter({ hasText: "更正" })
          .count();
        return {
          pass: inDrawer > 0 && onMain === 0,
          actual: { inDrawer, onMain },
        };
      },
    });
    await page.locator("#facts-close").click();
    await waitStable(page, 200);

    // A5 一路问到摘要
    const progress = await toSummary(
      page,
      ctx,
      sessionId,
      BLUE,
      "A",
      BLUE_ANSWERS,
      BLUE_CHOICES,
    );
    rec.record({ group: "A", persona: BLUE, action: `到摘要：${progress}` });
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A5-summary-card",
      {
        full: false,
      },
    );

    // A6 摘要卡上的更正入口（与抽屉里的同一动作，叫法是否一致）
    await page.locator("#toggle-facts").click();
    await waitStable(page, 200);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A6-facts-drawer",
    );
    await check(page, rec, {
      id: "R3-A6-summary-edit",
      severity: "P3",
      group: "A",
      persona: BLUE,
      expected:
        "摘要卡提供逐项更正（这是好的），但同一动作在抽屉里叫「更正」、未知项叫「补充 / 更正」",
      run: async () => {
        const edits = await page
          .locator("#messages button.summary-edit")
          .count();
        const labels = await page
          .locator("#messages button.summary-edit")
          .allTextContents();
        return {
          pass: edits > 0,
          actual: { edits, labels: [...new Set(labels.map((t) => t.trim()))] },
        };
      },
    });
    await check(page, rec, {
      id: "R3-A6-drawer-language",
      severity: "P2",
      group: "A",
      persona: BLUE,
      expected:
        "「已记录信息」抽屉用患者语言显示，不出现 sex/male/warning_* 这类内部字段名",
      run: async () => {
        const text = await page.locator("#fact-list").innerText();
        const hits =
          text.match(/\b(sex|male|female|self|child|warning_[^\s，。]+)\b/g) ??
          [];
        return {
          pass: hits.length === 0,
          actual: { hits: [...new Set(hits)], sample: text.slice(0, 120) },
        };
      },
    });
    await correctFact(page, ctx, sessionId, BLUE, "A", "影响程度", "7分");
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A6-after-correct",
    );

    // A7 改主诉 → 旧答案被撤回？有没有告诉患者？
    await correctFact(
      page,
      ctx,
      sessionId,
      BLUE,
      "A",
      "主要问题",
      "最近三天太阳穴胀痛，越来越重",
    );
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A7-after-chief-change",
    );
    const toastText = await page
      .locator("#toast")
      .innerText()
      .catch(() => "");
    const state = await stateOf(page, sessionId);
    const withdrawn = ["onset", "trajectory", "severity"].filter(
      (key) => state.facts?.[key]?.assertion !== "present",
    );
    await check(page, rec, {
      id: "R3-H1-silent-withdraw",
      severity: "P1",
      group: "A",
      persona: BLUE,
      expected:
        "改主诉后旧答案被撤回、急症筛查被重问，界面要说明「哪些回答需要重来、为什么」",
      run: async () => {
        const pageText = await page
          .locator("#messages, #active-question")
          .innerText()
          .catch(() => "");
        const explained = /撤回|需要重新|重新回答|受影响的内容/.test(pageText);
        return {
          pass: withdrawn.length === 0 || explained,
          actual: {
            withdrawn,
            explained,
            toast: toastText,
            note: "唯一反馈是 toast，且说的是「摘要已重新整理」",
          },
        };
      },
    });
    rec.record({
      group: "A",
      persona: BLUE,
      action: "撤回检查",
      detail: `被撤回：${withdrawn.join(",") || "无"}`,
    });

    // A8 再答到摘要
    const again = await toSummary(
      page,
      ctx,
      sessionId,
      BLUE,
      "A",
      BLUE_ANSWERS,
      BLUE_CHOICES,
    );
    rec.record({ group: "A", persona: BLUE, action: `回到摘要：${again}` });
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A8-summary-again",
      {
        full: false,
      },
    );
    await check(page, rec, {
      id: "R3-A8-naming",
      severity: "P3",
      group: "A",
      persona: BLUE,
      expected:
        "同一个动作在不同位置的叫法一致（抽屉「更正」/ 摘要「更正」/「补充 / 更正」）",
      run: async () => {
        const labels = await page
          .locator("#messages .summary-edit, #fact-list button.fact-edit")
          .allTextContents();
        const editLabels = labels
          .map((text) => text.trim())
          .filter((text) => text.includes("更正"));
        const unique = [...new Set(editLabels)];
        return {
          pass: unique.length <= 1,
          actual: { unique, all: [...new Set(labels.map((t) => t.trim()))] },
        };
      },
    });

    // A8b 摘要卡里的字段呈现（单独取证：性别一行显示成什么）
    const summaryCard = page.locator("#messages .summary-card").last();
    await summaryCard.scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(200);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A8-summary-facts",
      {
        force: true,
        full: false,
      },
    );

    // A9 确认 → 结论单 + 随访
    const before = await replyCount(page, sessionId);
    await clickButton(page, "确认摘要");
    await waitTurn(page, sessionId, before, 30000);
    await waitStable(page, 300);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A9-conclusion",
      { full: false },
    );
    await check(page, rec, {
      id: "R3-A9-conclusion",
      severity: "P1",
      group: "A",
      persona: BLUE,
      expected: "确认后出现结论单与随访安排",
      run: async () => ({
        pass: await conclusionVisible(page),
        actual: {
          conclusion: await conclusionVisible(page),
          followUp: await page
            .getByText("例行回访", { exact: false })
            .first()
            .isVisible()
            .catch(() => false),
          phase: (await stateOf(page, sessionId)).phase,
        },
      }),
    });
    await check(page, rec, {
      id: "R3-A9-overflow",
      severity: "P2",
      group: "A",
      persona: BLUE,
      expected: "结论单在 1280 宽下没有横向溢出",
      run: () => noOverflow(page, "#messages *"),
    });
    await check(page, rec, {
      id: "R3-A9-tokens",
      severity: "P1",
      group: "A",
      persona: BLUE,
      expected: "结论单不出现内部枚举（self/stable/null…）",
      run: () => noInternalTokens(page, "#messages"),
    });

    // A10 刷新已结束的会话
    await page.reload();
    await waitStable(page, 800);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: BLUE, group: "A" },
      "A10-after-reload",
    );
    await check(page, rec, {
      id: "R3-H4-closed-reload",
      severity: "P2",
      group: "A",
      persona: BLUE,
      expected:
        "已结束的会话刷新后仍能回到刚才的结论（与「进行中自动恢复」一致）",
      run: async () => ({
        pass: await conclusionVisible(page),
        actual: {
          welcome: await isVisible(page, "#welcome"),
          conclusion: await conclusionVisible(page),
          hash: await page.evaluate(() => location.hash),
        },
      }),
    });
  } finally {
    await ctx.close(page);
  }
}

/** 把一条主诉推进到"安全筛查已否认"的状态。 */
async function openToComplaint(
  page,
  ctx,
  persona,
  group,
  { subject, sex, age, complaint, base },
) {
  const sessionId = await startSession(page, ctx.rec, persona, group, base);
  ctx.track(persona, sessionId);
  await choose(page, ctx.rec, sessionId, persona, group, subject);
  await choose(page, ctx.rec, sessionId, persona, group, sex);
  await say(page, ctx.rec, sessionId, persona, group, age);
  await say(page, ctx.rec, sessionId, persona, group, complaint);
  return sessionId;
}

async function groupB(ctx) {
  const { base, rec } = ctx;
  const page = await ctx.page();
  try {
    // B1/B2 裸共现：先澄清
    const s1 = await openToComplaint(page, ctx, RED, "B", {
      subject: "我自己",
      sex: "男",
      age: "58岁",
      complaint: "胸口有点疼",
      base,
    });
    await waitStable(page, 300);
    await shotHere(page, rec, s1, { persona: RED, group: "B" }, "B1-clarify");
    const labels = await page
      .locator("#active-question button:visible, #quick-replies button:visible")
      .allTextContents();
    await check(page, rec, {
      id: "R3-B1-clarify",
      severity: "P1",
      group: "B",
      persona: RED,
      expected:
        "只有部位+信号、没有严重描述时，先出澄清卡（两个明确选项），不直接判急症",
      run: async () => ({
        pass:
          labels.some((t) => t.includes("是的")) &&
          labels.some((t) => t.includes("没有")),
        actual: { labels },
      }),
    });
    const before2 = await replyCount(page, s1);
    await clickButton(page, "没有，我没有这种情况");
    await waitTurn(page, s1, before2);
    await shotHere(page, rec, s1, { persona: RED, group: "B" }, "B2-denied");
    await check(page, rec, {
      id: "R3-B2-deny",
      severity: "P1",
      group: "B",
      persona: RED,
      expected: "否认后继续常规问诊，不留下急症结论",
      run: async () => ({
        pass:
          !(await isVisible(page, "#messages .emergency")) &&
          (await lastReply(page, s1))?.question_id != null,
        actual: {
          risk: (await stateOf(page, s1)).risk?.level,
          html: await isVisible(page, "#messages .emergency"),
        },
      }),
    });

    // B3 同一入口，改点"是的"
    const s2 = await openToComplaint(page, ctx, RED, "B", {
      subject: "我自己",
      sex: "男",
      age: "58岁",
      complaint: "胸口有点疼",
      base,
    });
    await waitStable(page, 300);
    const before3 = await replyCount(page, s2);
    await clickButton(page, "是的，确实有这种情况");
    await waitTurn(page, s2, before3);
    await shotHere(
      page,
      rec,
      s2,
      { persona: RED, group: "B" },
      "B3-confirmed-emergency",
      { full: false },
    );
    await check(page, rec, {
      id: "R3-B3-confirm",
      severity: "P1",
      group: "B",
      persona: RED,
      expected: "确认后立即给急救指引（含 120）",
      run: async () => {
        const text = await page.locator("#messages").innerText();
        return { pass: text.includes("120"), actual: text.slice(0, 200) };
      },
    });

    // B4 明确急症：一句话直达
    const s3 = await openToComplaint(page, ctx, RED, "B", {
      subject: "我自己",
      sex: "男",
      age: "58岁",
      complaint: "胸口像压了块大石头，喘不上气，一直出冷汗",
      base,
    });
    await waitStable(page, 300);
    await shotHere(
      page,
      rec,
      s3,
      { persona: RED, group: "B" },
      "B4-direct-emergency",
      { full: false },
    );
    await check(page, rec, {
      id: "R3-B4-direct",
      severity: "P0",
      group: "B",
      persona: RED,
      expected: "明确的急症描述一轮直达急救，不先进澄清",
      run: async () => {
        const text = await page.locator("#messages").innerText();
        return { pass: text.includes("120"), actual: text.slice(0, 220) };
      },
    });

    // B5 紧急延迟门：疼到 8 分，界面还在问
    const s4 = await openToComplaint(page, ctx, RED, "B", {
      subject: "我自己",
      sex: "男",
      age: "35岁",
      complaint: "肚子疼得厉害，两天了",
      base,
    });
    await waitStable(page, 300);
    await shotHere(
      page,
      rec,
      s4,
      { persona: RED, group: "B" },
      "B5-before-severity",
    );
    let gateReply = null;
    let gateText = "";
    const gate = await answerUntil(page, rec, s4, RED, "B", {
      answers: {
        onset: "两天前突然开始",
        associated_symptoms: "没有呕吐，也没发烧",
        gi_details: "肚脐周围一阵阵绞着疼",
        medications_taken: "吃了点止痛药，没效果",
        allergies: "没有",
        relevant_history: "没有手术史",
        patient_goal: "疼得受不了，想知道要不要去急诊",
      },
      choices: { emergency_screen: "都没有", trajectory: "在加重" },
      max: 14,
      done: async () => {
        const reply = await lastReply(page, s4);
        return (
          reply?.kind === "urgent" ||
          reply?.kind === "summary" ||
          reply?.kind === "emergency"
        );
      },
      onStep: async (reply) => {
        if (await isVisible(page, "#active-task")) return false;
        if (reply.question_id === "severity") {
          await answerSeverity(page, ctx, s4, RED, "B", 8);
          // 8 分之后的第一句：它会不会解释"为什么还要问"？
          gateReply = await lastReply(page, s4);
          gateText = await page.locator("#messages").innerText();
          await shotHere(
            page,
            rec,
            s4,
            { persona: RED, group: "B" },
            "B5-after-severity-8",
          );
          return true;
        }
        return false;
      },
    });
    rec.record({ group: "B", persona: RED, action: `紧急路径结果：${gate}` });
    await shotHere(
      page,
      rec,
      s4,
      { persona: RED, group: "B" },
      "B5-urgent-or-summary",
      { full: false },
    );
    const afterGate = gateReply;
    await check(page, rec, {
      id: "R3-H5-defer-gate",
      severity: "P1",
      group: "B",
      persona: RED,
      expected:
        "疼到 8 分仍在追问时，界面要说明「为什么还要问、以及现在就可以去」",
      run: async () => {
        const reply = gateReply ?? (await lastReply(page, s4));
        const text = gateText || (await page.locator("#messages").innerText());
        const explains = /继续问|还需要确认|现在就|不必等|先补充|尽快就医/.test(
          text,
        );
        return {
          pass: reply?.kind !== "question" || explains,
          actual: {
            kind: reply?.kind,
            questionId: reply?.question_id,
            explains,
          },
        };
      },
    });

    // B6 孩子 40.2℃
    const s5 = await openToComplaint(page, ctx, AMBER, "B", {
      subject: "孩子",
      sex: "男",
      age: "3岁",
      complaint: "孩子发烧一天，最高40度2",
      base,
    });
    await waitStable(page, 300);
    await shotHere(
      page,
      rec,
      s5,
      { persona: AMBER, group: "B" },
      "B6-fever-start",
    );
    const fever = await answerUntil(page, rec, s5, AMBER, "B", {
      answers: {
        onset: "昨天下午开始",
        associated_symptoms: "精神不太好，其他没有",
        resp_breathing: "呼吸正常，没有喘",
        resp_cough_details: "偶尔咳两声",
        resp_intake: "还能喝水，吃得少",
        reported_temperature: "40.2℃",
        medications_taken: "吃了退烧药",
        allergies: "没有",
        relevant_history: "足月顺产，没有基础病",
        patient_goal: "想知道要不要马上去医院",
      },
      choices: { emergency_screen: "都没有", trajectory: "在加重" },
      max: 16,
      done: async () => {
        const reply = await lastReply(page, s5);
        return (
          reply?.kind === "urgent" ||
          reply?.kind === "summary" ||
          reply?.kind === "emergency"
        );
      },
      onStep: async (reply) => {
        if (await isVisible(page, "#active-task")) {
          await completeTemperatureTask(page, ctx, s5, AMBER, "B", "40.2");
          return true;
        }
        if (reply.question_id === "severity") {
          await answerSeverity(page, ctx, s5, AMBER, "B", 7);
          return true;
        }
        return false;
      },
    });
    rec.record({
      group: "B",
      persona: AMBER,
      action: `高热路径结果：${fever}`,
    });
    await shotHere(
      page,
      rec,
      s5,
      { persona: AMBER, group: "B" },
      "B6-fever-outcome",
      { full: false },
    );
    await check(page, rec, {
      id: "R3-B6-fever",
      severity: "P0",
      group: "B",
      persona: AMBER,
      expected: "3 岁孩子 40.2℃ 应尽快升级，不停在自护",
      run: async () => {
        const state = await stateOf(page, s5);
        const reply = await lastReply(page, s5);
        const text = await page.locator("#messages").innerText();
        return {
          pass:
            ["urgent", "emergency"].includes(reply?.kind) ||
            state.risk?.level === "urgent",
          actual: {
            kind: reply?.kind,
            risk: state.risk?.level,
            hasToday: /今天|尽快|急诊/.test(text),
          },
        };
      },
    });
  } finally {
    await ctx.close(page);
  }
}

export const groups = { A: groupA, B: groupB };
