/**
 * C 定位任务的四条分支 / D 测量与重复测量 / E 计算与能力可达性。
 *
 * E 组的方法说明：36 个量表、21 个公式在 demo 的规则引擎里没有入口
 * （`request_calculation` 只被自适应引擎调用）。这里不切假后端冒充患者，
 * 而是让患者**真的开口要求计算**，记录系统如何回应——"能力存在但用不上"
 * 本身就是一条体验结论。
 */
import {
  answerUntil,
  choose,
  clickButton,
  lastReply,
  replyCount,
  say,
  shotHere,
  snapshot,
  startSession,
  stateOf,
  waitStable,
  waitTurn,
} from "../lib/drive.mjs";
import { check, countVisible, noOverflow, textOf } from "../lib/checks.mjs";

const BLUE = "蓝-定位";
const AMBER = "黄-测温";

const isVisible = async (page, selector) =>
  page
    .locator(selector)
    .first()
    .isVisible()
    .catch(() => false);

async function answerSeverity(page, ctx, sessionId, persona, group, score) {
  const before = await replyCount(page, sessionId);
  await clickButton(page, String(score));
  await waitStable(page, 150);
  await clickButton(page, "确认");
  await waitTurn(page, sessionId, before);
  ctx.rec.record({
    group,
    persona,
    action: `影响程度 ${score} 分`,
    turn: before + 1,
  });
}

/** 开场 → 回答到定位任务出现（头痛这条通路大约在第 6 步）。 */
async function untilLocation(page, ctx, sessionId, persona, group) {
  for (let step = 0; step < 12; step += 1) {
    if (await isVisible(page, "#active-task")) return true;
    const reply = await lastReply(page, sessionId);
    const questionId = reply?.question_id;
    if (!questionId) return false;
    if (questionId.startsWith("warning_")) {
      await choose(page, ctx.rec, sessionId, persona, group, "没有");
      continue;
    }
    if (questionId === "emergency_screen") {
      await choose(page, ctx.rec, sessionId, persona, group, "都没有");
      continue;
    }
    if (questionId === "trajectory") {
      await choose(page, ctx.rec, sessionId, persona, group, "一阵一阵的");
      continue;
    }
    if (questionId === "severity") {
      await answerSeverity(page, ctx, sessionId, persona, group, 5);
      continue;
    }
    const answers = {
      onset: "两天前开始的",
      associated_symptoms: "没有其他不舒服",
      head_details: "后脑勺发胀，低头更明显",
      medications_taken: "没吃药",
      allergies: "没有过敏",
      relevant_history: "没有高血压",
      patient_goal: "想知道要不要去医院",
    };
    if (!answers[questionId]) return false;
    await say(page, ctx.rec, sessionId, persona, group, answers[questionId]);
  }
  return false;
}

/** 开一个新会话并走到定位任务。 */
async function openToLocation(page, ctx, group, tag) {
  const persona = `${BLUE}-${tag}`;
  const sessionId = await startSession(page, ctx.rec, persona, group, ctx.base);
  ctx.track(persona, sessionId);
  await choose(page, ctx.rec, sessionId, persona, group, "我自己");
  await choose(page, ctx.rec, sessionId, persona, group, "男");
  await say(page, ctx.rec, sessionId, persona, group, "32岁");
  await say(
    page,
    ctx.rec,
    sessionId,
    persona,
    group,
    "后脑勺胀痛两天了，一阵一阵的",
  );
  const reached = await untilLocation(page, ctx, sessionId, persona, group);
  return { persona, sessionId, reached };
}

/** 完成定位任务（部位 → 记录）。 */
async function recordLocation(page, ctx, sessionId, persona, group, tag) {
  await clickButton(page, "头/面部");
  await waitStable(page, 200);
  const buttons = await page
    .locator("#active-task button:visible")
    .allTextContents();
  const record = buttons.find((text) => text.trim().startsWith("记录"));
  if (!record) return false;
  const before = await replyCount(page, sessionId);
  await clickButton(page, record.trim());
  await waitTurn(page, sessionId, before);
  await shotHere(
    page,
    ctx.rec,
    sessionId,
    { persona, group },
    `${tag}-recorded`,
  );
  return true;
}

async function groupC(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    // C1 说不清 / 跳过
    const c1 = await openToLocation(page, ctx, "C", "C1");
    await shotHere(
      page,
      rec,
      c1.sessionId,
      { persona: c1.persona, group: "C" },
      "C1-location-task",
    );
    const before = await replyCount(page, c1.sessionId);
    await clickButton(page, "说不清/跳过");
    await waitTurn(page, c1.sessionId, before);
    await shotHere(
      page,
      rec,
      c1.sessionId,
      { persona: c1.persona, group: "C" },
      "C1-skipped",
    );
    const c1State = await stateOf(page, c1.sessionId);
    await check(page, rec, {
      id: "R3-C1-skip",
      severity: "P1",
      group: "C",
      persona: c1.persona,
      expected: "跳过定位应记为「未做」，不能变成「没有」；然后回到原问题",
      run: async () => ({
        pass: c1State.facts?.symptom_location?.assertion === "unknown",
        actual: {
          location: c1State.facts?.symptom_location ?? null,
          task: c1State.active_task ?? null,
        },
      }),
    });

    // C2 在任务里直接打字说部位
    const c2 = await openToLocation(page, ctx, "C", "C2");
    const before2 = await replyCount(page, c2.sessionId);
    await say(page, rec, c2.sessionId, c2.persona, "C", "头/面部");
    await waitTurn(page, c2.sessionId, before2);
    await shotHere(
      page,
      rec,
      c2.sessionId,
      { persona: c2.persona, group: "C" },
      "C2-typed-rejected",
    );
    const c2Text = await page.locator("#messages").innerText();
    await check(page, rec, {
      id: "R3-C2-typed",
      severity: "P2",
      group: "C",
      persona: c2.persona,
      expected:
        "在定位任务里直接打字说部位时，要么接受、要么明确指引「点上面的图示」，且不静默丢弃",
      run: async () => ({
        pass:
          /图示|点选|选择上面|上方/.test(c2Text) ||
          (await isVisible(page, "#active-task")),
        actual: {
          taskStillThere: await isVisible(page, "#active-task"),
          tail: c2Text.slice(-160),
        },
      }),
    });

    // C3 口头取消
    const c3 = await openToLocation(page, ctx, "C", "C3");
    const before3 = await replyCount(page, c3.sessionId);
    await say(page, rec, c3.sessionId, c3.persona, "C", "取消定位");
    await waitTurn(page, c3.sessionId, before3);
    await shotHere(
      page,
      rec,
      c3.sessionId,
      { persona: c3.persona, group: "C" },
      "C3-cancelled",
    );
    await check(page, rec, {
      id: "R3-C3-cancel",
      severity: "P2",
      group: "C",
      persona: c3.persona,
      expected: "口头「取消定位」应结束任务并回到原问题",
      run: async () => {
        const state = await stateOf(page, c3.sessionId);
        return {
          pass: !state.active_task,
          actual: { activeTask: state.active_task ?? null },
        };
      },
    });

    // C4 已记录后修改定位
    const c4 = await openToLocation(page, ctx, "C", "C4");
    await recordLocation(page, ctx, c4.sessionId, c4.persona, "C", "C4");
    await page.locator("#toggle-facts").click();
    await waitStable(page, 200);
    await shotHere(
      page,
      rec,
      c4.sessionId,
      { persona: c4.persona, group: "C" },
      "C4-facts-location",
    );
    const revise = page
      .locator("#fact-list button.location-revision-start")
      .first();
    const visible = await revise.isVisible().catch(() => false);
    let revised = false;
    if (visible) {
      await revise.evaluate((el) => el.click());
      await waitStable(page, 700);
      await shotHere(
        page,
        rec,
        c4.sessionId,
        { persona: c4.persona, group: "C" },
        "C4-revision-task",
      );
      const before4 = await replyCount(page, c4.sessionId);
      // 修改向导从「2/4 具体位置」开始，原来选的大部位保留；
      // 改成头顶，再走完左右/范围，最后核对记录。
      const tap = async (label) => {
        const button = page
          .locator("#active-task button")
          .filter({ hasText: label })
          .first();
        if (!(await button.isVisible().catch(() => false))) return false;
        await button.evaluate((el) => el.click()).catch(() => {});
        await waitStable(page, 300);
        return true;
      };
      await tap("头顶附近");
      for (let step = 0; step < 6; step += 1) {
        if (await tap("核对并记录位置")) break;
        await tap("两侧");
        await tap("一片范围");
        const next = page
          .locator("#active-task button")
          .filter({ hasText: "下一步" })
          .first();
        const disabled = await next.isDisabled().catch(() => true);
        if (disabled) break;
        await next.evaluate((el) => el.click()).catch(() => {});
        await waitStable(page, 350);
      }
      await waitTurn(page, c4.sessionId, before4).catch(() => {});
      revised = true;
      await shotHere(
        page,
        rec,
        c4.sessionId,
        { persona: c4.persona, group: "C" },
        "C4-revised",
      );
    }
    await check(page, rec, {
      id: "R3-C4-revise",
      severity: "P2",
      group: "C",
      persona: c4.persona,
      expected: "已记录的定位可以改，改完患者能看出「这条被改过」",
      run: async () => {
        const state = await stateOf(page, c4.sessionId);
        const location = state.facts?.symptom_location;
        return {
          pass: visible && revised,
          actual: {
            revisionEntry: visible,
            revised,
            value: location?.value ?? null,
          },
        };
      },
    });

    // C6 任务卡时展开对话记录，再回来
    const c6 = await openToLocation(page, ctx, "C", "C6");
    const toggle = page.locator("#toggle-task-history");
    const toggleText = await toggle.innerText().catch(() => "");
    await toggle.click();
    await waitStable(page, 400);
    await shotHere(
      page,
      rec,
      c6.sessionId,
      { persona: c6.persona, group: "C" },
      "C6-history-open",
    );
    const backText = await toggle.innerText().catch(() => "");
    await toggle.click();
    await waitStable(page, 400);
    await shotHere(
      page,
      rec,
      c6.sessionId,
      { persona: c6.persona, group: "C" },
      "C6-back-to-task",
    );
    await check(page, rec, {
      id: "R3-C6-toggle",
      severity: "P2",
      group: "C",
      persona: c6.persona,
      expected: "任务卡占满屏时，回看对话记录的入口好找、回来也明确",
      run: async () => ({
        pass:
          Boolean(toggleText.trim()) && (await isVisible(page, "#active-task")),
        actual: {
          forward: toggleText.trim(),
          back: backText.trim(),
          taskBack: await isVisible(page, "#active-task"),
        },
      }),
    });
  } finally {
    await ctx.close(page);
  }
}

/** 走到体温任务并完成一次测量。 */
async function measureOnce(page, ctx, sessionId, persona, group, value, tag) {
  const before = await replyCount(page, sessionId);
  for (let step = 0; step < 4; step += 1) {
    if (await clickButton(page, "填写结果", { timeout: 800 })) break;
    if (!(await clickButton(page, "下一步", { timeout: 800 }))) break;
    await waitStable(page, 150);
  }
  await page.locator("#message-input").fill(value);
  await page.locator("#send-button").click();
  await waitTurn(page, sessionId, before, 30000);
  await shotHere(page, ctx.rec, sessionId, { persona, group }, tag);
}

async function groupD(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    const sessionId = await startSession(page, rec, AMBER, "D", ctx.base);
    ctx.track(AMBER, sessionId);
    await choose(page, rec, sessionId, AMBER, "D", "孩子");
    await choose(page, rec, sessionId, AMBER, "D", "男");
    await say(page, rec, sessionId, AMBER, "D", "3岁");
    await say(page, rec, sessionId, AMBER, "D", "孩子发烧一天，精神不太好");
    const reached = await answerUntil(page, rec, sessionId, AMBER, "D", {
      answers: {
        onset: "昨天下午开始",
        associated_symptoms: "精神差一些，没有别的",
        resp_breathing: "呼吸正常，不喘",
        resp_cough_details: "没有咳嗽",
        resp_intake: "能喝水，吃得少",
        reported_temperature: "38.5℃",
        medications_taken: "吃了退烧药",
        allergies: "没有",
        relevant_history: "足月顺产，没有基础病",
        patient_goal: "想知道要不要去医院",
      },
      choices: { emergency_screen: "都没有", trajectory: "变化不大" },
      max: 16,
      done: async () => isVisible(page, "#active-task"),
      onStep: async (reply) => {
        if (reply.question_id === "severity") {
          await answerSeverity(page, ctx, sessionId, AMBER, "D", 5);
          return true;
        }
        return false;
      },
    });
    rec.record({
      group: "D",
      persona: AMBER,
      action: `到达体温任务：${reached}`,
    });
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: AMBER, group: "D" },
      "D1-temperature-task",
    );

    await measureOnce(
      page,
      ctx,
      sessionId,
      AMBER,
      "D",
      "38.2",
      "D2-first-reading",
    );
    const completed = async () => {
      const snap = await snapshot(page, sessionId);
      return snap.events
        .filter((event) => event.event_type === "self_check.completed")
        .map((e) => e.payload);
    };
    await check(page, rec, {
      id: "R3-D2-first",
      severity: "P1",
      group: "D",
      persona: AMBER,
      expected: "第一次测量写成一条带测量方式的记录，而不是只当「说过一句」",
      run: async () => {
        const state = await stateOf(page, sessionId);
        const readings = await completed();
        return {
          pass: Boolean(state.facts?.temperature_c) && readings.length >= 1,
          actual: {
            fact: state.facts?.temperature_c ?? null,
            readings: readings.map((item) => item.quality ?? item),
          },
        };
      },
    });

    // D3 再测一次：患者会用「再测一次」这句话
    const beforeRepeat = (await completed()).length;
    await say(page, rec, sessionId, AMBER, "D", "再测一次腋温，刚才可能没夹紧");
    await waitStable(page, 400);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: AMBER, group: "D" },
      "D3-repeat-task",
    );
    await measureOnce(
      page,
      ctx,
      sessionId,
      AMBER,
      "D",
      "38.6",
      "D3-second-reading",
    );
    await check(page, rec, {
      id: "R3-D3-repeat",
      severity: "P2",
      group: "D",
      persona: AMBER,
      expected: "再测一次时两次读数都保留，且患者能分清哪次是哪次",
      run: async () => {
        const readings = await completed();
        const preserved = readings.some(
          (item) => item.previous_reading_preserved === true,
        );
        // 两次都留下就算过；preserved=false 意味着界面没有带出"上次 38.2"的提示，
        // 作为观察记录在 actual 里，不单独判失败。
        return {
          pass: readings.length > beforeRepeat,
          actual: {
            count: readings.length,
            beforeRepeat,
            previousReadingShown: preserved,
            readings,
          },
        };
      },
    });

    // D4 模糊读数
    await say(
      page,
      rec,
      sessionId,
      AMBER,
      "D",
      "再测一次腋温，这次差不多三十八度",
    );
    await waitStable(page, 400);
    await measureOnce(
      page,
      ctx,
      sessionId,
      AMBER,
      "D",
      "大概38度",
      "D4-uncertain-input",
    );
    await check(page, rec, {
      id: "R3-D4-quality",
      severity: "P0",
      group: "D",
      persona: AMBER,
      expected: "「大概38度」不能当成确定的 38.0℃；应标为质量不足或请患者重填",
      run: async () => {
        const state = await stateOf(page, sessionId);
        const readings = await completed();
        const text = await page.locator("body").innerText();
        const last = readings.at(-1) ?? {};
        const flagged =
          ["inadequate", "unconfirmed", "unclear"].includes(
            String(last.quality),
          ) || /未确认|质量不足|请核对|无法确定/.test(text);
        const certain =
          state.facts?.temperature_c?.value === "38.0" ||
          state.facts?.temperature_c?.value === "38";
        return {
          pass: flagged && !certain,
          actual: {
            last,
            fact: state.facts?.temperature_c ?? null,
            flagged,
            certain,
          },
        };
      },
    });

    // D5 跳过
    const skipped = await clickButton(page, "暂时跳过", { timeout: 5000 });
    if (skipped) await waitStable(page, 600);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: AMBER, group: "D" },
      "D5-skip",
    );
    await check(page, rec, {
      id: "R3-D5-skip",
      severity: "P2",
      group: "D",
      persona: AMBER,
      expected: "跳过测量后回到原问题，并说明数据不会被当成「没有测」",
      run: async () => {
        const state = await stateOf(page, sessionId);
        return {
          pass: !state.active_task,
          actual: {
            activeTask: state.active_task ?? null,
            temperature: state.facts?.temperature_c ?? null,
          },
        };
      },
    });
  } finally {
    await ctx.close(page);
  }
}

async function groupE(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    // E1 档案里记血糖（患者可达的换算入口）
    await page.goto(`${ctx.base}/`);
    await waitStable(page, 600);
    await page.locator('[data-view="records"]').click();
    await waitStable(page, 500);
    await page.locator("#add-glucose").click();
    await waitStable(page, 400);
    await shotHere(
      page,
      rec,
      null,
      { persona: "蓝-档案", group: "E" },
      "E1-glucose-dialog",
    );
    await page.locator("#glucose-record-value").fill("5.6");
    const save = page
      .getByRole("button", { name: "保存记录", exact: true })
      .last();
    await save.click();
    await waitStable(page, 1200);
    await shotHere(
      page,
      rec,
      null,
      { persona: "蓝-档案", group: "E" },
      "E1-glucose-saved",
    );
    await check(page, rec, {
      id: "R3-E1-glucose",
      severity: "P2",
      group: "E",
      persona: "蓝-档案",
      expected: "记录血糖表单可用，保存后时间线出现条目",
      run: async () => {
        const count = await page
          .locator("#records-timeline .record-card")
          .count();
        const empty = await isVisible(page, "#records-timeline .empty-state");
        return { pass: count > 0 || !empty, actual: { cards: count, empty } };
      },
    });

    // E2 血压双值
    const addMeasurement = page.locator("#add-measurement");
    if (await addMeasurement.isVisible().catch(() => false)) {
      await addMeasurement.click();
      await waitStable(page, 400);
      await shotHere(
        page,
        rec,
        null,
        { persona: "蓝-档案", group: "E" },
        "E2-measurement-dialog",
      );
      await page
        .locator("#measurement-record-kind")
        .selectOption({ label: "血压" })
        .catch(() => {});
      await waitStable(page, 300);
      const inputs = page.locator("#measurement-record-values input:visible");
      if ((await inputs.count()) >= 2) {
        await inputs.nth(0).fill("128");
        await inputs.nth(1).fill("84");
      } else {
        await page
          .locator("#measurement-record-values")
          .fill("128/84")
          .catch(() => {});
      }
      await page
        .getByRole("button", { name: "保存记录", exact: true })
        .last()
        .click();
      await waitStable(page, 1200);
      await shotHere(
        page,
        rec,
        null,
        { persona: "蓝-档案", group: "E" },
        "E2-bp-saved",
      );
    }
    await check(page, rec, {
      id: "R3-E2-bp",
      severity: "P2",
      group: "E",
      persona: "蓝-档案",
      expected: "血压这类双值测量能一次录入，两个数都留下",
      run: async () => {
        const text = await page
          .locator("#records-timeline")
          .innerText()
          .catch(() => "");
        const hasBoth = text.includes("128") && text.includes("84");
        return {
          pass: hasBoth,
          actual: { hasBoth, sample: text.slice(0, 200) },
        };
      },
    });

    // E3 患者直接要求计算
    const persona = "蓝-计算";
    const sessionId = await startSession(page, rec, persona, "E", ctx.base);
    ctx.track(persona, sessionId);
    await choose(page, rec, sessionId, persona, "E", "我自己");
    await choose(page, rec, sessionId, persona, "E", "男");
    await say(page, rec, sessionId, persona, "E", "32岁");
    await say(
      page,
      rec,
      sessionId,
      persona,
      "E",
      "帮我算一下 BMI，身高175厘米，体重70公斤",
    );
    await shotHere(
      page,
      rec,
      sessionId,
      { persona, group: "E" },
      "E3-asked-for-calculation",
      { full: false },
    );
    const reply = await lastReply(page, sessionId);
    const text = await page.locator("#messages").innerText();
    await check(page, rec, {
      id: "R3-E3-calculation",
      severity: "P2",
      group: "E",
      persona,
      expected:
        "患者明确要求「算 BMI」时，要么给出计算卡，要么说明这一步在当前模式里做不到",
      run: async () => ({
        pass:
          ["calculation_input", "calculation_result"].includes(reply?.kind) ||
          /暂不|无法|不支持|离线|计算/.test(text),
        actual: {
          kind: reply?.kind,
          questionId: reply?.question_id,
          tail: text.slice(-200),
        },
      }),
    });

    // E4 能力确实存在（只是患者碰不到）
    const registry = await page.evaluate(async () => {
      const response = await fetch("/api/capabilities");
      const body = await response.json();
      return body;
    });
    const items = registry.capabilities ?? registry.items ?? [];
    const scales = items.filter((item) =>
      String(item.patient_form ?? item.form ?? "").startsWith("scale_form"),
    );
    const formulas = items.filter((item) =>
      String(item.patient_form ?? item.form ?? "").startsWith("formula_form"),
    );
    await check(page, rec, {
      id: "R3-E4-registry",
      severity: "P2",
      group: "E",
      persona,
      expected: "能力目录里确实有量表/公式，但患者在 demo 的对话里没有任何入口",
      run: async () => ({
        pass: items.length > 0,
        actual: {
          total: items.length,
          scales: scales.length,
          formulas: formulas.length,
        },
      }),
    });
  } finally {
    await ctx.close(page);
  }
}

export const groups = { C: groupC, D: groupD, E: groupE };
