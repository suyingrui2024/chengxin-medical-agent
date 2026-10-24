import { expect, test } from "@playwright/test";

// S2 结论卡验收：默认只展开处置 + 叙事两段（urgent 时安全网为例外展开），
// 其余区段收起带计数且 aria-expanded=false；全部展开后文本无丢失。
function makeSheet(level) {
  return {
    version: 1,
    tier: level === "urgent_today" ? "urgent" : "routine",
    disposition: {
      level,
      text: "可以在家先观察处理",
      window_hours: 72,
    },
    directions_visible: true,
    subject_kind: "self",
    narrative: {
      framing: "这次的情况属于可以在家先观察处理的类型。",
      severity: "目前程度较轻，重点是观察变化。",
      reasoning: "发热与咳嗽同时出现。",
    },
    directions: [
      {
        group: "likely",
        certainty: "possible",
        patient_label: "上呼吸道感染",
        support_quotes: ["咳嗽三天"],
        against_note: null,
      },
    ],
    gaps: [{ label: "不适部位", impact: "这项信息会影响判断" }],
    self_care: {
      do: ["在安静、光线柔和的环境休息"],
      avoid: ["不要自行长期服用止痛药"],
      measure: ["记录体温变化"],
    },
    return_now_if: ["意识改变", "呼吸困难"],
    outlook: "这类不适通常休息后可缓解",
    follow_up: { kind: "routine_checkin", due_in_hours: 72 },
    further_reading: {
      citations: [
        {
          title: "上肢不适自我护理要点",
          url: "https://example.org/care",
          publisher: "示例来源",
          fetched_at: "2026-10-24",
          tier: "T1",
          // 校验器要求引用句必为字符串（真实后端引用句总有该字段）。
          quote: "休息与观察是首要处理。",
        },
      ],
    },
  };
}

async function renderSheet(page, level) {
  return page.evaluate(async (sheet) => {
    const module = await import("/structured-ui.js");
    const renderer = module.createRenderer();
    const fragment = renderer.renderEnvelope(
      module.replyEnvelope({ action_id: "s2", message: "", conclusion: sheet }),
    );
    document.body.replaceChildren(fragment);
    const card = document.querySelector(".conclusion-card");
    const toggles = [
      ...card.querySelectorAll(".conclusion-collapse-toggle"),
    ].map((toggle) => ({
      text: toggle.textContent || "",
      expanded: toggle.getAttribute("aria-expanded"),
    }));
    return {
      text: card.textContent || "",
      toggles,
      dispositionVisible: Boolean(
        card.querySelector(".conclusion-disposition-text")?.getClientRects()
          .length,
      ),
      narrativeVisible: Boolean(
        card.querySelector(".conclusion-narrative-framing")?.getClientRects()
          .length,
      ),
      gapsVisible: Boolean(
        card.querySelector(".conclusion-gaps")?.getClientRects().length,
      ),
      safetyVisible: Boolean(
        card.querySelector(".summary-safety")?.getClientRects().length,
      ),
    };
  }, makeSheet(level));
}

test("S2 conclusion card expands disposition and narrative only", async ({
  page,
}) => {
  await page.goto("/");
  const result = await renderSheet(page, "self_care");
  expect(result.dispositionVisible).toBe(true);
  expect(result.narrativeVisible).toBe(true);
  expect(result.gapsVisible).toBe(false);
  expect(result.safetyVisible).toBe(false);
  // 六个可收起区段：方向、缺口、自护、安全网、接下来、延伸阅读。
  expect(result.toggles).toHaveLength(6);
  expect(result.toggles.map((t) => t.expanded)).toEqual([
    "false",
    "false",
    "false",
    "false",
    "false",
    "false",
  ]);
  expect(result.toggles.map((t) => t.text)).toEqual([
    "可能的方向（不是确诊） · 1",
    "还缺的信息 · 1",
    "现在能做的事 · 3",
    "出现以下变化，请立即求助 · 2",
    "接下来 · 2",
    "延伸阅读 · 1",
  ]);
  // 收起只是披露状态：文本仍在 DOM 内，无信息丢失。
  for (const fragment of [
    "上呼吸道感染",
    "不适部位",
    "在安静、光线柔和的环境休息",
    "不要自行长期服用止痛药",
    "意识改变",
    "这类不适通常休息后可缓解",
    "上肢不适自我护理要点",
  ]) {
    expect(result.text).toContain(fragment);
  }
});

test("S2 urgent disposition keeps the safety net expanded", async ({
  page,
}) => {
  await page.goto("/");
  const result = await renderSheet(page, "urgent_today");
  expect(result.dispositionVisible).toBe(true);
  expect(result.narrativeVisible).toBe(true);
  expect(result.safetyVisible).toBe(true);
  expect(result.gapsVisible).toBe(false);
  const safety = result.toggles.find((t) => t.text.startsWith("出现以下变化"));
  expect(safety.expanded).toBe("true");
});

test("S2 expanding all toggles reveals every section", async ({ page }) => {
  await page.goto("/");
  const expanded = await page.evaluate(async (sheet) => {
    const module = await import("/structured-ui.js");
    const renderer = module.createRenderer();
    const fragment = renderer.renderEnvelope(
      module.replyEnvelope({ action_id: "s2", message: "", conclusion: sheet }),
    );
    document.body.replaceChildren(fragment);
    const card = document.querySelector(".conclusion-card");
    for (const toggle of card.querySelectorAll(".conclusion-collapse-toggle")) {
      toggle.click();
    }
    return {
      toggles: [...card.querySelectorAll(".conclusion-collapse-toggle")].map(
        (toggle) => toggle.getAttribute("aria-expanded"),
      ),
      gapsVisible: Boolean(
        card.querySelector(".conclusion-gaps")?.getClientRects().length,
      ),
      safetyVisible: Boolean(
        card.querySelector(".summary-safety")?.getClientRects().length,
      ),
      readingVisible: Boolean(
        card.querySelector(".conclusion-reading")?.getClientRects().length,
      ),
    };
  }, makeSheet("self_care"));
  expect(expanded.toggles).toEqual([
    "true",
    "true",
    "true",
    "true",
    "true",
    "true",
  ]);
  expect(expanded.gapsVisible).toBe(true);
  expect(expanded.safetyVisible).toBe(true);
  expect(expanded.readingVisible).toBe(true);
});
