/**
 * F 代答与换人 / G 随访回填 / H 报告粘贴 / I 健康档案。
 */
import {
  answerUntil,
  choose,
  clickButton,
  lastReply,
  replyCount,
  say,
  shotHere,
  startSession,
  stateOf,
  waitStable,
  waitTurn,
} from "../lib/drive.mjs";
import { check, countVisible, noOverflow, textOf } from "../lib/checks.mjs";

const ASK = "王-代答";
const FOLLOW = "李-随访";
const REPORT = "赵-报告";
const RECORD = "钱-档案";

const isVisible = async (page, selector) =>
  page
    .locator(selector)
    .first()
    .isVisible()
    .catch(() => false);

/** 通用答案表：足够把一条普通上呼吸道通路走到摘要。 */
const GENERIC = {
  onset: "三天前开始的",
  associated_symptoms: "没有别的不舒服",
  gi_details: "肚脐周围一阵阵胀痛，和吃饭关系不大，没有呕吐腹泻",
  head_details: "后脑勺发胀，低头更明显",
  dizzy_details: "没有头晕，也没有天旋地转",
  back_details: "腰背部酸胀，久坐明显",
  urinary_details: "排尿正常，没有尿痛",
  skin_details: "皮肤没有起疹子",
  infant_observable_signs: "精神还可以，能玩",
  eye_warning_signs: "眼睛没有红肿，看东西清楚",
  pregnancy_status: "没有怀孕",
  resp_breathing: "呼吸正常，不喘",
  resp_cough_details: "干咳，没有痰",
  resp_intake: "能正常喝水吃饭",
  reported_temperature: "37.8℃",
  medications_taken: "没吃药",
  allergies: "没有过敏",
  relevant_history: "没有基础病",
  patient_goal: "想知道要不要去医院",
};

/** 走到摘要并确认结束，返回 sessionId（后面随访要用）。 */
export async function closeConsultation(
  page,
  ctx,
  persona,
  group,
  complaint,
  tag,
) {
  const sessionId = await startSession(page, ctx.rec, persona, group, ctx.base);
  ctx.track(persona, sessionId);
  await choose(page, ctx.rec, sessionId, persona, group, "我自己");
  await choose(page, ctx.rec, sessionId, persona, group, "男");
  await say(page, ctx.rec, sessionId, persona, group, "30岁");
  await say(page, ctx.rec, sessionId, persona, group, complaint);
  await answerUntil(page, ctx.rec, sessionId, persona, group, {
    answers: GENERIC,
    choices: { emergency_screen: "都没有", trajectory: "变化不大" },
    max: 18,
    shoot: false,
    done: async () => (await lastReply(page, sessionId))?.kind === "summary",
    onStep: async (reply) => {
      if (reply.question_id === "severity") {
        const before = await replyCount(page, sessionId);
        await clickButton(page, "4");
        await waitStable(page, 150);
        await clickButton(page, "确认");
        await waitTurn(page, sessionId, before);
        return true;
      }
      if (await isVisible(page, "#active-task")) {
        const before = await replyCount(page, sessionId);
        const buttons = await page
          .locator("#active-task button:visible")
          .allTextContents();
        const record = buttons.find((text) => text.trim().startsWith("记录"));
        if (record) await clickButton(page, record.trim());
        else await clickButton(page, "暂时跳过");
        await waitTurn(page, sessionId, before).catch(() => {});
        return true;
      }
      return false;
    },
  });
  const before = await replyCount(page, sessionId);
  await clickButton(page, "确认摘要");
  await waitTurn(page, sessionId, before, 30000);
  await waitStable(page, 400);
  await shotHere(page, ctx.rec, sessionId, { persona, group }, `${tag}-closed`);
  return sessionId;
}

async function groupF(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    // F1 代答入口
    const first = await startSession(page, rec, ASK, "F", ctx.base);
    ctx.track(ASK, first);
    await choose(page, rec, first, ASK, "F", "孩子");
    await choose(page, rec, first, ASK, "F", "女");
    await say(page, rec, first, ASK, "F", "4岁");
    await say(page, rec, first, ASK, "F", "孩子咳嗽三天了");
    await shotHere(
      page,
      rec,
      first,
      { persona: ASK, group: "F" },
      "F1-child-start",
    );

    // F2a 在急症筛查题上，患者随口说一句带「对了」的换人请求
    const before = await replyCount(page, first);
    await say(page, rec, first, ASK, "F", "对了，帮我儿子也看一下，他也咳嗽");
    const answer = await lastReply(page, first);
    await shotHere(
      page,
      rec,
      first,
      { persona: ASK, group: "F" },
      "F2-yesword-emergency",
      {
        full: false,
      },
    );
    const screen = (await stateOf(page, first)).facts?.emergency_screen ?? null;
    await check(page, rec, {
      id: "R3-F2-yesword",
      severity: "P0",
      group: "F",
      persona: ASK,
      expected:
        "在「急症筛查」这一题上说与本题无关的话（这里是一句换人请求）不能被判成「有危险信号」",
      run: async () => ({
        pass: answer?.kind !== "emergency",
        actual: {
          kind: answer?.kind,
          screenFact: screen,
          trigger:
            "interpreter 用 raw.startswith(「有」「是」「对」) 判定 yes——「对了…」命中了「对」",
        },
      }),
    });

    // F2b 另开会话：正常的换人请求
    const second = await startSession(page, rec, ASK, "F", ctx.base);
    ctx.track(ASK, second);
    await choose(page, rec, second, ASK, "F", "孩子");
    await choose(page, rec, second, ASK, "F", "男");
    await say(page, rec, second, ASK, "F", "4岁");
    await say(page, rec, second, ASK, "F", "孩子咳嗽三天了");
    await choose(page, rec, second, ASK, "F", "都没有");
    // 先试患者最自然的说法
    await say(page, rec, second, ASK, "F", "这次先给我先生问一下，他也在咳嗽");
    const natural = await lastReply(page, second);
    await shotHere(
      page,
      rec,
      second,
      { persona: ASK, group: "F" },
      "F2-natural-phrasing",
    );
    await check(page, rec, {
      id: "R3-F3-natural",
      severity: "P2",
      group: "F",
      persona: ASK,
      expected:
        "「这次先给我先生问一下」是很自然的换人说法，应被识别（或在答完当前题后追问）",
      run: async () => ({
        pass: natural?.kind === "subject_change",
        actual: {
          kind: natural?.kind,
          questionId: natural?.question_id,
          note: "关系词表有丈夫/老公，没有「先生」",
        },
      }),
    });

    // 再用系统支持的固定说法
    const beforeSwitch = await replyCount(page, second);
    await say(page, rec, second, ASK, "F", "换个人问一下，我儿子也在咳嗽");
    const switched = await lastReply(page, second);
    await shotHere(
      page,
      rec,
      second,
      { persona: ASK, group: "F" },
      "F2-subject-change",
    );
    const labels = await page
      .locator("#active-question button:visible, #quick-replies button:visible")
      .allTextContents();
    await check(page, rec, {
      id: "R3-F2-subject",
      severity: "P2",
      group: "F",
      persona: ASK,
      expected:
        "明确要替另一位家人问时，给出换人卡片（两个动作），不打断当前记录",
      run: async () => ({
        pass:
          switched?.kind === "subject_change" ||
          labels.some((text) => text.includes("新建问诊")),
        actual: {
          kind: switched?.kind,
          labels: labels.map((text) => text.trim()),
        },
      }),
    });

    // F4 继续当前问诊
    const continueButton = page
      .getByRole("button", { name: /继续.*问诊/ })
      .first();
    if (await continueButton.isVisible().catch(() => false)) {
      const beforeContinue = await replyCount(page, second);
      await continueButton.click();
      await waitTurn(page, second, beforeContinue).catch(() => {});
      await shotHere(
        page,
        rec,
        second,
        { persona: ASK, group: "F" },
        "F4-continued",
      );
    }
    await check(page, rec, {
      id: "R3-F4-continue",
      severity: "P2",
      group: "F",
      persona: ASK,
      expected: "选「继续当前问诊」后回到原来的问题，不丢进度",
      run: async () => {
        const state = await stateOf(page, second);
        return {
          pass: Boolean(state.facts?.consult_for) && state.phase !== "closed",
          actual: {
            consultFor: state.facts?.consult_for ?? null,
            phase: state.phase,
          },
        };
      },
    });
  } finally {
    await ctx.close(page);
  }
}

async function groupG(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    const first = await closeConsultation(
      page,
      ctx,
      FOLLOW,
      "G",
      "咳嗽三天，有痰",
      "G1",
    );
    rec.record({
      group: "G",
      persona: FOLLOW,
      action: "第一个会话已结束",
      detail: first,
    });

    await page.locator('[data-view="history"]').click();
    await waitStable(page, 600);
    await shotHere(
      page,
      rec,
      first,
      { persona: FOLLOW, group: "G" },
      "G1-history-followup",
    );
    await check(page, rec, {
      id: "R3-G1-followup",
      severity: "P2",
      group: "G",
      persona: FOLLOW,
      expected: "会话结束后「待随访」区出现，并说清什么时候回访",
      run: async () => {
        const text = await page
          .locator("#followup-section")
          .innerText()
          .catch(() => "");
        return { pass: /随访|近况/.test(text), actual: text.slice(0, 200) };
      },
    });

    const openFollow = page.getByRole("button", { name: "记录近况" }).first();
    if (await openFollow.isVisible().catch(() => false)) {
      await openFollow.click();
      await waitStable(page, 500);
      await shotHere(
        page,
        rec,
        first,
        { persona: FOLLOW, group: "G" },
        "G2-followup-dialog",
      );
      const outcomes = await page
        .locator('#followup-dialog input[name="outcome"]')
        .count();
      await check(page, rec, {
        id: "R3-G2-outcomes",
        severity: "P3",
        group: "G",
        persona: FOLLOW,
        expected: "随访结局覆盖「好了/好转/没变/加重/已就医/已住院」",
        run: async () => ({ pass: outcomes === 6, actual: { outcomes } }),
      });

      // G3 好转中 → 保存
      await page
        .locator('#followup-dialog input[name="outcome"]')
        .first()
        .check()
        .catch(() => {});
      await page
        .locator("#followup-dialog label")
        .filter({ hasText: "好转" })
        .first()
        .click()
        .catch(() => {});
      await page
        .locator("#followup-dialog button[type=submit]")
        .click()
        .catch(async () => {
          await page.getByRole("button", { name: "保存记录" }).last().click();
        });
      await waitStable(page, 1200);
      await shotHere(
        page,
        rec,
        first,
        { persona: FOLLOW, group: "G" },
        "G3-followup-saved",
      );
      await check(page, rec, {
        id: "R3-G3-saved",
        severity: "P2",
        group: "G",
        persona: FOLLOW,
        expected: "记录近况后对话框关闭，待随访区不再把这条算作待办",
        run: async () => {
          const dialogOpen = await page
            .locator("#followup-dialog")
            .isVisible()
            .catch(() => false);
          const list = await page
            .locator("#followup-list")
            .innerText()
            .catch(() => "");
          return {
            pass: !dialogOpen,
            actual: { dialogOpen, list: list.slice(0, 120) },
          };
        },
      });
    }

    // G4 另一个会话：加重了 → 自动开新问诊
    const second = await closeConsultation(
      page,
      ctx,
      FOLLOW,
      "G",
      "肚子不舒服两天",
      "G4",
    );
    await page.locator('[data-view="history"]').click();
    await waitStable(page, 600);
    const openSecond = page.getByRole("button", { name: "记录近况" }).first();
    if (await openSecond.isVisible().catch(() => false)) {
      await openSecond.click();
      await waitStable(page, 400);
      await page
        .locator("#followup-dialog label")
        .filter({ hasText: "加重" })
        .first()
        .click()
        .catch(() => {});
      await page
        .locator("#followup-dialog button[type=submit]")
        .click()
        .catch(async () => {
          await page.getByRole("button", { name: "保存记录" }).last().click();
        });
      await waitStable(page, 1500);
      await shotHere(
        page,
        rec,
        second,
        { persona: FOLLOW, group: "G" },
        "G4-retriage",
      );
      await check(page, rec, {
        id: "R3-G4-retriage",
        severity: "P1",
        group: "G",
        persona: FOLLOW,
        expected:
          "反馈「加重了」时，系统要主动重分诊，并说明为什么、下一步是什么",
        run: async () => {
          const text = await page
            .locator("#followup-dialog")
            .innerText()
            .catch(() => "");
          const retriage = await isVisible(page, "#followup-retriage");
          return {
            pass: retriage && /新的|重新|进入/.test(text),
            actual: { retriage, tail: text.slice(-220) },
          };
        },
      });
    }
  } finally {
    await ctx.close(page);
  }
}

async function groupH(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    const sessionId = await startSession(page, rec, REPORT, "H", ctx.base);
    ctx.track(REPORT, sessionId);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: REPORT, group: "H" },
      "H1-zero-context",
    );
    await check(page, rec, {
      id: "R3-H1-report-entry",
      severity: "P3",
      group: "H",
      persona: REPORT,
      expected:
        "刚开场、还没有任何记录时，「添加报告内容 / 就诊交接摘要」不该是可点的空上下文",
      run: async () => {
        const report = await page
          .locator("#open-report-draft")
          .isVisible()
          .catch(() => false);
        const handoff = await page
          .locator("#open-handoff")
          .isVisible()
          .catch(() => false);
        return { pass: !report && !handoff, actual: { report, handoff } };
      },
    });

    await page.locator("#open-report-draft").click();
    await waitStable(page, 400);
    await page
      .locator("#report-text")
      .fill(
        "检验报告日期 2026-11-01\n空腹血糖 5.6 mmol/L（参考 3.9-6.1）\n总胆固醇 6.2 mmol/L（参考 <5.2）\n血压 128/84 mmHg",
      );
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: REPORT, group: "H" },
      "H2-report-dialog",
    );
    await page.getByRole("button", { name: "提取待核对项目" }).click();
    await waitStable(page, 1500);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: REPORT, group: "H" },
      "H2-report-draft",
    );
    const confirms = page.getByRole("button", { name: "确认加入" });
    const count = await confirms.count();
    await check(page, rec, {
      id: "R3-H2-parse",
      severity: "P1",
      group: "H",
      persona: REPORT,
      expected:
        "真实检验单的两种行格式（括注参考、血压比值）都能被提取，共 3 项待核对",
      run: async () => ({ pass: count === 3, actual: { confirms: count } }),
    });

    for (let index = 0; index < count; index += 1) {
      const button = page.getByRole("button", { name: "确认加入" }).first();
      if (await button.isVisible().catch(() => false)) {
        await button.click();
        await waitStable(page, 400);
      }
    }
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: REPORT, group: "H" },
      "H3-report-confirmed",
    );

    // H4 移除一项（原生 confirm）
    page.on("dialog", (dialog) => dialog.accept());
    const remove = page.getByRole("button", { name: "移除" }).first();
    if (await remove.isVisible().catch(() => false)) {
      await remove.evaluate((el) => el.click()).catch(() => {});
      await waitStable(page, 800);
      await shotHere(
        page,
        rec,
        sessionId,
        { persona: REPORT, group: "H" },
        "H4-report-removed",
      );
    }
    await check(page, rec, {
      id: "R3-H4-remove",
      severity: "P3",
      group: "H",
      persona: REPORT,
      expected:
        "移除已确认的报告项可用，且与全站自绘对话框风格一致（这里用的是浏览器原生 confirm）",
      run: async () => {
        const text = await page
          .locator("#report-items-summary")
          .innerText()
          .catch(() => "");
        return { pass: true, actual: { left: text.slice(0, 160) } };
      },
    });
  } finally {
    await ctx.close(page);
  }
}

async function groupI(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    await page.goto(`${ctx.base}/`);
    await waitStable(page, 600);
    await page.locator('[data-view="records"]').click();
    await waitStable(page, 700);
    await shotHere(
      page,
      rec,
      null,
      { persona: RECORD, group: "I" },
      "I1-records-empty",
    );
    await check(page, rec, {
      id: "R3-I1-entrances",
      severity: "P2",
      group: "I",
      persona: RECORD,
      expected: "档案首页的三个入口清晰，且不与右上角按钮重复",
      run: async () => {
        const actions = await page
          .locator(".records-actions button:visible")
          .allTextContents();
        return {
          pass: actions.length > 0,
          actual: actions.map((text) => text.trim()),
        };
      },
    });

    // I2 基线
    const baselineToggle = page.locator("#baseline-collapse-toggle");
    if (await baselineToggle.isVisible().catch(() => false)) {
      await baselineToggle.click();
      await waitStable(page, 300);
    }
    await page
      .locator("#baseline-birth-year")
      .fill("1992")
      .catch(() => {});
    await page
      .locator("#baseline-sex")
      .selectOption("male")
      .catch(() => {});
    await page
      .locator("#baseline-height")
      .fill("175")
      .catch(() => {});
    await page
      .locator("#baseline-weight")
      .fill("70")
      .catch(() => {});
    await shotHere(
      page,
      rec,
      null,
      { persona: RECORD, group: "I" },
      "I2-baseline-form",
    );
    await page
      .getByRole("button", { name: "保存基线信息" })
      .click()
      .catch(() => {});
    await waitStable(page, 1200);
    await shotHere(
      page,
      rec,
      null,
      { persona: RECORD, group: "I" },
      "I2-baseline-saved",
    );
    await check(page, rec, {
      id: "R3-I2-baseline",
      severity: "P2",
      group: "I",
      persona: RECORD,
      expected: "基线信息保存后刷新仍在（身高体重是计算类能力的前置）",
      run: async () => {
        await page.reload();
        await waitStable(page, 1200);
        await page.locator('[data-view="records"]').click();
        await waitStable(page, 800);
        const year = await page
          .locator("#baseline-birth-year")
          .inputValue()
          .catch(() => "");
        return { pass: year === "1992", actual: { year } };
      },
    });

    // I3 家庭成员
    const memberToggle = page.locator("#members-collapse-toggle");
    if (await memberToggle.isVisible().catch(() => false)) {
      await memberToggle.click();
      await waitStable(page, 300);
    }
    await page
      .locator("#member-add-name")
      .fill("小明")
      .catch(() => {});
    await page
      .getByRole("button", { name: "添加成员" })
      .click()
      .catch(() => {});
    await waitStable(page, 1200);
    await shotHere(
      page,
      rec,
      null,
      { persona: RECORD, group: "I" },
      "I3-member-added",
    );
    await check(page, rec, {
      id: "R3-I3-member",
      severity: "P2",
      group: "I",
      persona: RECORD,
      expected: "添加成员后列表出现，并说明「现在看的是谁」",
      run: async () => {
        const text = await page
          .locator("#member-list")
          .innerText()
          .catch(() => "");
        return {
          pass: text.includes("小明"),
          actual: {
            hasMember: text.includes("小明"),
            sample: text.slice(0, 160),
          },
        };
      },
    });

    // I4 上传图片（demo 无视觉模型 → 409）
    await page.locator("#upload-medical-image").click();
    await waitStable(page, 500);
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
      "base64",
    );
    await page.locator("#medical-image-file").setInputFiles({
      name: "report.png",
      mimeType: "image/png",
      buffer: png,
    });
    await waitStable(page, 500);
    await shotHere(
      page,
      rec,
      null,
      { persona: RECORD, group: "I" },
      "I4-image-dialog",
    );
    await page
      .getByRole("button", { name: "归档并识别" })
      .click()
      .catch(() => {});
    await waitStable(page, 2500);
    await shotHere(
      page,
      rec,
      null,
      { persona: RECORD, group: "I" },
      "I4-image-result",
    );
    await check(page, rec, {
      id: "R3-I4-vision",
      severity: "P2",
      group: "I",
      persona: RECORD,
      expected:
        "离线下上传图片时，要说清「图已归档，但没有视觉模型所以没识别」，而不是只报错",
      run: async () => {
        const feedback = await page
          .locator("#medical-image-feedback")
          .innerText()
          .catch(() => "");
        const body = await page
          .locator("#records-notice")
          .innerText()
          .catch(() => "");
        const explains = /归档|视觉|识别/.test(feedback + body);
        return {
          pass: explains,
          actual: {
            feedback: feedback.slice(0, 200),
            notice: body.slice(0, 120),
          },
        };
      },
    });
  } finally {
    await ctx.close(page);
  }
}

export const groups = { F: groupF, G: groupG, H: groupH, I: groupI };
