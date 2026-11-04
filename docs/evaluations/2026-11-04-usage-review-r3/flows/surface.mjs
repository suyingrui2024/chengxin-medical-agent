/**
 * J 主题与字号 / K 打印与三通道导出 / L 窄屏与大字 / M 错误态。
 *
 * K 组的重点不是「按钮在不在」，而是**打开导出物读内容**：复制到剪贴板
 * 的文字、下载下来的 .md/.txt、打印窗口里的页面，各自是不是对患者说话。
 */
import {
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
import {
  check,
  clipboardText,
  downloadText,
  minTarget,
  noInternalTokens,
  noOverflow,
} from "../lib/checks.mjs";
import { closeConsultation } from "./periphery.mjs";

const VIEW = "孙-界面";
const EXPORT = "周-导出";
const NARROW = "吴-窄屏";

const isVisible = async (page, selector) =>
  page
    .locator(selector)
    .first()
    .isVisible()
    .catch(() => false);

async function groupJ(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    await page.goto(`${ctx.base}/`);
    await waitStable(page, 600);
    await page.locator('[data-view="settings"]').click();
    await waitStable(page, 600);
    await shotHere(
      page,
      rec,
      null,
      { persona: VIEW, group: "J" },
      "J1-settings-appearance",
    );

    // J1 切主题：夜航（深色）
    const night = page
      .locator(".appearance-option")
      .filter({ hasText: "夜航" })
      .first();
    if (await night.isVisible().catch(() => false)) {
      await night.click();
      await waitStable(page, 500);
    }
    await shotHere(
      page,
      rec,
      null,
      { persona: VIEW, group: "J" },
      "J1-theme-night",
      { force: true },
    );
    const theme = await page.evaluate(
      () => document.documentElement.dataset.appearance,
    );
    await check(page, rec, {
      id: "R3-J1-theme",
      severity: "P2",
      group: "J",
      persona: VIEW,
      expected: "外观切换即时生效（夜航为深色）",
      run: async () => ({ pass: theme === "night", actual: { theme } }),
    });

    // J2 字号「大」
    const large = page
      .locator(".font-option, .appearance-option")
      .filter({ hasText: "大" })
      .first();
    if (await large.isVisible().catch(() => false)) {
      await large.click();
      await waitStable(page, 400);
    }
    const fontScale = await page.evaluate(
      () => document.documentElement.dataset.fontScale,
    );
    await shotHere(
      page,
      rec,
      null,
      { persona: VIEW, group: "J" },
      "J2-font-large",
      { force: true },
    );

    // J3 刷新后保持
    await page.reload();
    await waitStable(page, 900);
    const after = await page.evaluate(() => ({
      theme: document.documentElement.dataset.appearance,
      font: document.documentElement.dataset.fontScale,
    }));
    await shotHere(
      page,
      rec,
      null,
      { persona: VIEW, group: "J" },
      "J3-after-reload",
    );
    await check(page, rec, {
      id: "R3-J3-persist",
      severity: "P2",
      group: "J",
      persona: VIEW,
      expected: "「下次打开会保留」要真的保留（主题与字号刷新后仍生效）",
      run: async () => ({
        pass: after.theme === "night" && after.font === fontScale,
        actual: { ...after, fontScaleAtSwitch: fontScale },
      }),
    });

    // J4 患者误入研发界面后能否回来
    await page.locator('[data-view="settings"]').click();
    await waitStable(page, 500);
    const mode = page.locator("#workspace-mode");
    await mode.selectOption("developer").catch(() => {});
    await waitStable(page, 700);
    await shotHere(
      page,
      rec,
      null,
      { persona: VIEW, group: "J" },
      "J4-developer-mode",
    );
    const devNav = await page
      .locator('[data-view="execution"]')
      .isVisible()
      .catch(() => false);
    await mode.selectOption("patient").catch(() => {});
    await waitStable(page, 500);
    await page
      .locator('[data-view="consultation"]')
      .click()
      .catch(() => {});
    await waitStable(page, 400);
    await shotHere(
      page,
      rec,
      null,
      { persona: VIEW, group: "J" },
      "J4-back-to-patient",
    );
    await check(page, rec, {
      id: "R3-J4-mode",
      severity: "P2",
      group: "J",
      persona: VIEW,
      expected: "患者能自己切到「完整研发界面」，也要能明确切回来",
      run: async () => {
        const back = await page
          .locator('[data-view="execution"]')
          .isVisible()
          .catch(() => false);
        return {
          pass: devNav && !back,
          actual: { devNav, stillVisibleAfterSwitchBack: back },
        };
      },
    });
  } finally {
    await ctx.close(page);
  }
}

async function groupK(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    const sessionId = await closeConsultation(
      page,
      ctx,
      EXPORT,
      "K",
      "咳嗽三天，有痰",
      "K1",
    );
    await page.locator("#open-handoff").click();
    await waitStable(page, 700);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: EXPORT, group: "K" },
      "K1-handoff",
    );
    const buttons = await page
      .locator("#handoff-dialog button:visible")
      .allTextContents();
    await check(page, rec, {
      id: "R3-K1-channels",
      severity: "P2",
      group: "K",
      persona: EXPORT,
      expected:
        "交接摘要给出去就诊的四个通道（复制/打印/.md/.txt）与免责声明齐全",
      run: async () => {
        const text = await page.locator("#handoff-dialog").innerText();
        const wanted = ["复制", "打印", ".md", ".txt"];
        return {
          pass:
            wanted.every((word) => text.includes(word)) &&
            /医生|参考/.test(text),
          actual: {
            buttons: buttons.map((item) => item.trim()),
            disclaimer: /医生/.test(text),
          },
        };
      },
    });

    // K2 复制内容
    await page.locator("#copy-handoff").click();
    await waitStable(page, 500);
    const copied = await clipboardText(page);
    await check(page, rec, {
      id: "R3-K2-copy",
      severity: "P1",
      group: "K",
      persona: EXPORT,
      expected: "复制出来的摘要不出现内部枚举（self/male/stable/null）",
      run: async () => {
        const text = String(copied.actual ?? "");
        const hit =
          /\b(self|child|male|female|stable|worsening|null|undefined|None)\b/.exec(
            text,
          );
        return {
          pass: !hit,
          actual: {
            hit: hit ? hit[0] : null,
            chars: text.length,
            sample: text.slice(0, 160),
          },
        };
      },
    });

    // K4/K5 导出物内容
    const md = await downloadText(page, () =>
      page.locator("#export-handoff-md").click(),
    );
    const txt = await downloadText(page, () =>
      page.locator("#export-handoff-txt").click(),
    );
    await check(page, rec, {
      id: "R3-K4-export",
      severity: "P1",
      group: "K",
      persona: EXPORT,
      expected:
        "导出的 .md / .txt 都是完整摘要，且不含内部枚举；.txt 不应残留 Markdown 记号",
      run: async () => {
        const mdText = String(md.actual?.text ?? "");
        const txtText = String(txt.actual?.text ?? "");
        const internal =
          /\b(self|child|male|female|stable|worsening|null|undefined)\b/.test(
            mdText + txtText,
          );
        return {
          pass: mdText.length > 80 && txtText.length > 80 && !internal,
          actual: {
            md: {
              name: md.actual?.name,
              chars: mdText.length,
              head: mdText.slice(0, 80),
            },
            txt: {
              name: txt.actual?.name,
              chars: txtText.length,
              hasMarkdown: /^#{1,3} /m.test(txtText),
            },
            internal,
          },
        };
      },
    });

    // K3 打印页
    let printed = null;
    try {
      const [popup] = await Promise.all([
        page.waitForEvent("popup", { timeout: 8000 }),
        page.locator("#print-handoff").click(),
      ]);
      await popup.waitForLoadState("domcontentloaded").catch(() => {});
      await popup.waitForTimeout(600);
      await popup.evaluate(() => document.body.innerText.slice(0, 1200));
      rec.record({ group: "K", persona: EXPORT, action: "打印页已打开" });
      await popup
        .screenshot({
          path: `${ctx.dir}/screenshots/r3-print-page.png`,
          fullPage: true,
        })
        .catch(() => {});
      printed = popup;
    } catch {
      printed = null;
    }
    await check(page, rec, {
      id: "R3-K3-print",
      severity: "P2",
      group: "K",
      persona: EXPORT,
      expected:
        "打印页能打开、内容完整、带免责声明；被浏览器拦截时要有替代路径",
      run: async () => ({
        pass: Boolean(printed),
        actual: printed
          ? { opened: true }
          : {
              opened: false,
              note: "未捕获到打印窗口（可能被拦截），界面无任何提示",
            },
      }),
    });
    if (printed) await printed.close().catch(() => {});
  } finally {
    await ctx.close(page);
  }
}

async function groupL(ctx) {
  const { rec } = ctx;
  const page = await ctx.page({ width: 390, height: 844 });
  try {
    const sessionId = await startSession(page, rec, NARROW, "L", ctx.base);
    ctx.track(NARROW, sessionId);
    await choose(page, rec, sessionId, NARROW, "L", "我自己");
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: NARROW, group: "L" },
      "L1-mobile-question",
    );
    await check(page, rec, {
      id: "R3-L1-mobile",
      severity: "P1",
      group: "L",
      persona: NARROW,
      expected: "390px 宽下问诊可读可点，没有横向溢出",
      run: async () => {
        const overflow = await noOverflow(page, "#view-consultation *");
        const targets = await minTarget(page, "#active-question button", 44);
        return {
          pass: overflow.pass,
          actual: { overflow: overflow.actual, smallTargets: targets.actual },
        };
      },
    });

    // L3 窄屏下的摘要/结论与交接
    await choose(page, rec, sessionId, NARROW, "L", "男");
    await say(page, rec, sessionId, NARROW, "L", "28岁");
    await say(page, rec, sessionId, NARROW, "L", "嗓子疼三天");
    const state = await stateOf(page, sessionId);
    rec.record({
      group: "L",
      persona: NARROW,
      action: "窄屏问诊进行中",
      detail: `phase=${state.phase}`,
    });
    await page
      .locator("#open-handoff")
      .click()
      .catch(() => {});
    await waitStable(page, 600);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: NARROW, group: "L" },
      "L3-mobile-handoff",
    );
    await check(page, rec, {
      id: "R3-L3-mobile-handoff",
      severity: "P2",
      group: "L",
      persona: NARROW,
      expected: "390px 下交接摘要的四个通道不溢出、可点",
      run: async () => {
        const overflow = await noOverflow(page, "#handoff-dialog *");
        return { pass: overflow.pass, actual: overflow.actual };
      },
    });
  } finally {
    await ctx.close(page);
  }
}

async function groupM(ctx) {
  const { rec } = ctx;
  const page = await ctx.page();
  try {
    const sessionId = await startSession(page, rec, VIEW, "M", ctx.base);
    ctx.track(VIEW, sessionId);
    await choose(page, rec, sessionId, VIEW, "M", "我自己");

    // M1 断网（拦掉 API）
    await page.route("**/api/**", (route) => route.abort());
    await page.reload();
    await waitStable(page, 1200);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: VIEW, group: "M" },
      "M1-offline",
    );
    await check(page, rec, {
      id: "R3-M1-offline",
      severity: "P1",
      group: "M",
      persona: VIEW,
      expected:
        "服务不可用时，错误条要说清「发生了什么、下一步怎么办」，而不是只留一个按钮",
      run: async () => {
        const status = await page
          .locator("#connection-status")
          .innerText()
          .catch(() => "");
        const alertText = await page
          .locator("#connection-alert")
          .innerText()
          .catch(() => "");
        const retry = await page
          .locator("#connection-retry")
          .isVisible()
          .catch(() => false);
        return {
          pass: alertText.trim().length > 0,
          actual: {
            status: status.trim(),
            alertText: alertText.trim(),
            retryVisible: retry,
          },
        };
      },
    });

    // M2 点重新连接（仍然断网）
    if (
      await page
        .locator("#connection-retry")
        .isVisible()
        .catch(() => false)
    ) {
      await page.locator("#connection-retry").click();
      await waitStable(page, 1500);
      const toast = await page
        .locator("#toast")
        .innerText()
        .catch(() => "");
      await shotHere(
        page,
        rec,
        sessionId,
        { persona: VIEW, group: "M" },
        "M2-retry-failed",
      );
      await check(page, rec, {
        id: "R3-M2-retry",
        severity: "P2",
        group: "M",
        persona: VIEW,
        expected:
          "重连失败时给出可执行的下一步（检查桌面应用/端口），而不只是一句「无法连接」",
        run: async () => ({
          pass: /端口|桌面|应用|地址|检查/.test(toast),
          actual: { toast: toast.trim() },
        }),
      });
    }

    // M3 恢复
    await page.unroute("**/api/**");
    await page.reload();
    await waitStable(page, 1500);
    await shotHere(
      page,
      rec,
      sessionId,
      { persona: VIEW, group: "M" },
      "M3-recovered",
    );
    await check(page, rec, {
      id: "R3-M3-recover",
      severity: "P2",
      group: "M",
      persona: VIEW,
      expected: "服务恢复后刷新能回到可用状态",
      run: async () => {
        const status = await page
          .locator("#connection-status")
          .innerText()
          .catch(() => "");
        return {
          pass: status.includes("已连接"),
          actual: { status: status.trim() },
        };
      },
    });

    // M4 不存在的会话
    await check(page, rec, {
      id: "R3-M4-404",
      severity: "P3",
      group: "M",
      persona: VIEW,
      expected: "打开不存在的会话时返回可理解的中文错误",
      run: async () => {
        const result = await page.evaluate(async () => {
          const response = await fetch("/api/sessions/does-not-exist/snapshot");
          return { status: response.status, body: await response.text() };
        });
        return {
          pass: result.status === 404 && result.body.includes("找不到"),
          actual: result,
        };
      },
    });

    // M5 双击发送
    const session2 = await startSession(page, rec, VIEW, "M", ctx.base);
    ctx.track(VIEW, session2);
    await choose(page, rec, session2, VIEW, "M", "我自己");
    await page.locator("#message-input").fill("后脑勺有点胀");
    const send = page.locator("#send-button");
    await send.click();
    await send.click({ force: true, timeout: 1200 }).catch(() => {});
    await waitStable(page, 1200);
    await shotHere(
      page,
      rec,
      session2,
      { persona: VIEW, group: "M" },
      "M5-double-send",
    );
    await check(page, rec, {
      id: "R3-M5-double",
      severity: "P2",
      group: "M",
      persona: VIEW,
      expected: "快速双击发送只产生一条用户消息（不会重复提交）",
      run: async () => {
        const snapped = await stateOf(page, session2);
        const text = await page.locator("#messages").innerText();
        const occurrences = (text.match(/后脑勺有点胀/g) ?? []).length;
        return {
          pass: occurrences <= 1,
          actual: { occurrences, phase: snapped.phase },
        };
      },
    });

    // M7 「最近问诊」列表能否区分同一主诉的多次问诊
    const recent = await page
      .locator("#recent-sessions .recent-item")
      .allTextContents()
      .catch(() => []);
    await check(page, rec, {
      id: "R3-M7-recent",
      severity: "P2",
      group: "M",
      persona: VIEW,
      expected:
        "同一主诉的多次问诊在「最近问诊」里可区分（时间/序号），否则患者分不清点哪一条",
      run: async () => {
        const titles = recent.map((text) => text.replace(/\s+/g, " ").trim());
        const unique = new Set(titles);
        return {
          pass: titles.length <= 1 || unique.size === titles.length,
          actual: {
            count: titles.length,
            unique: unique.size,
            sample: titles.slice(0, 5),
          },
        };
      },
    });

    // M6 已结束会话：输入区应当收起（不允许继续往已结束的问诊里写）
    const closed = await closeConsultation(
      page,
      ctx,
      VIEW,
      "M",
      "咳嗽三天，有痰",
      "M6",
    );
    await waitStable(page, 500);
    const composerVisible = await page
      .locator("#message-input")
      .isVisible()
      .catch(() => false);
    await shotHere(
      page,
      rec,
      closed,
      { persona: VIEW, group: "M" },
      "M6-after-close",
      { force: true },
    );
    await check(page, rec, {
      id: "R3-M6-closed",
      severity: "P2",
      group: "M",
      persona: VIEW,
      expected: "问诊结束后输入区收起，患者不会以為还能继续这段对话",
      run: async () => ({
        pass: !composerVisible,
        actual: {
          composerVisible,
          note: "服务端的「已结束」守护话术在 UI 上不可达；改了主诉想继续时，只能自己找到「新的问诊」",
        },
      }),
    });
  } finally {
    await ctx.close(page);
  }
}

export const groups = { J: groupJ, K: groupK, L: groupL, M: groupM };
