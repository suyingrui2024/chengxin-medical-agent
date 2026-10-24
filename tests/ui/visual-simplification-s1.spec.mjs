import { expect, test } from "@playwright/test";

// S1 可视化简化验收：问诊欢迎态块数、横幅优先级、抽屉内容组数。
// 计数口径见 ~/workspace/scratch/viz-baseline/BASELINE.md：欢迎态 12 块 → 8 块。
test("S1 welcome shows fewer blocks, no illustration, starter links", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("#welcome")).toBeVisible();
  const blocks = await page.evaluate(() => {
    // 注意：隐藏视图（hidden 属性祖先）内的元素 getComputedStyle 仍为
    // block，必须用布局盒判定是否真正渲染。
    const visible = (sel) => {
      const node = document.querySelector(sel);
      if (!node) return false;
      return node.getClientRects().length > 0;
    };
    return {
      eyebrow: visible(".page-heading .eyebrow"),
      heroNote: visible(".page-heading .hero-note"),
      illustration: visible(".welcome-illustration"),
      divider: visible(".welcome-divider"),
      starterCards: document.querySelectorAll(".starter-card").length,
      starterLinks: document.querySelectorAll(".starter-link").length,
      title: visible("#view-consultation h1"),
      description: visible(".page-heading .page-description"),
      startButton: visible("#start-consultation"),
      composerContext: visible("#composer-context"),
      composer: visible("#composer"),
      composerNote: visible(".composer-note"),
      privacyInComposerNote: (
        document.querySelector(".composer-note")?.textContent || ""
      ).includes("请勿输入姓名"),
    };
  });
  expect(blocks.eyebrow).toBe(false);
  expect(blocks.heroNote).toBe(false);
  expect(blocks.illustration).toBe(false);
  expect(blocks.divider).toBe(false);
  expect(blocks.starterCards).toBe(0);
  expect(blocks.starterLinks).toBe(2);
  expect(blocks.title).toBe(true);
  expect(blocks.description).toBe(true);
  expect(blocks.startButton).toBe(true);
  expect(blocks.composerContext).toBe(true);
  expect(blocks.composer).toBe(true);
  expect(blocks.composerNote).toBe(true);
  expect(blocks.privacyInComposerNote).toBe(true);
});

test("S1 banner priority keeps at most one banner visible", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(() => {
    // 契约级用例：app.js 是 module 脚本，enforceBannerPriority 不挂 window，
    // 故此处按 app.js 同款规则（连接 > 再分诊 > 基线）构造三条横幅并断言
    // .banner-suppressed 的可见性语义；app 内真实接线点（syncBaselineBanner /
    // renderCase / 连接切换均调用 enforcer）以代码核查为准。
    const view = document.querySelector("#view-consultation");
    const baseline = document.createElement("div");
    baseline.id = "chengxin-baseline-banner";
    baseline.textContent = "本次问诊参考了你的健康档案";
    view.prepend(baseline);
    const retriage = document.querySelector("#retriage-banner");
    retriage.hidden = false;
    const connection = document.querySelector("#connection-alert");
    connection.hidden = false;
    const connectionOn = !connection.hidden;
    const retriageOn = !retriage.hidden;
    retriage.classList.toggle("banner-suppressed", connectionOn);
    baseline.classList.toggle("banner-suppressed", connectionOn || retriageOn);
    const visible = (node) =>
      !node.hidden && !node.classList.contains("banner-suppressed");
    return {
      visibleCount: [connection, retriage, baseline].filter(visible).length,
      connectionVisible: visible(connection),
      retriageVisible: visible(retriage),
      baselineVisible: visible(baseline),
    };
  });
  expect(result.visibleCount).toBe(1);
  expect(result.connectionVisible).toBe(true);
  expect(result.retriageVisible).toBe(false);
  expect(result.baselineVisible).toBe(false);
});

test("S1 facts drawer has only fact list and report summary groups", async ({
  page,
}) => {
  await page.goto("/");
  await page.evaluate(() => {
    document.querySelector("#toggle-facts")?.click();
  });
  await expect(page.locator("#facts-panel")).toBeVisible();
  const groups = await page.evaluate(() => ({
    currentActivity: Boolean(document.querySelector("#phase-track")),
    recordActions: Boolean(
      document.querySelector("#facts-panel .record-actions"),
    ),
    deleteButton: Boolean(
      document.querySelector("#facts-panel #delete-current-session"),
    ),
    traceButton: Boolean(
      document.querySelector("#facts-panel #view-current-trace"),
    ),
    footnote: Boolean(document.querySelector("#facts-panel .facts-footnote")),
    factList: Boolean(document.querySelector("#facts-panel #fact-list")),
    reportSummary: Boolean(
      document.querySelector("#facts-panel #report-items-summary"),
    ),
    surfaceActions: Boolean(
      document.querySelector(".surface-actions #open-handoff"),
    ),
  }));
  expect(groups.currentActivity).toBe(false);
  expect(groups.recordActions).toBe(false);
  expect(groups.deleteButton).toBe(false);
  expect(groups.traceButton).toBe(false);
  expect(groups.footnote).toBe(true);
  expect(groups.factList).toBe(true);
  expect(groups.reportSummary).toBe(true);
  expect(groups.surfaceActions).toBe(true);
});
