import { test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import { readFile } from "node:fs/promises";

// Conclusion card v1.3 (§13) regressions: the seven-segment order,
// restricted-mode direction rendering with the rule_out group first
// and prominent, the empty-directions absence rule, the strict
// department gate, the visit-notes section, the static closing line
// per disposition level, and the §13.5(a) reminder row (auto-scheduled
// follow-up + a button that focuses the existing follow-up card).
// The harness mirrors conclusion-card.spec.mjs: a scripted-model
// consultation is driven to a close, then the stored session is
// reopened with a constructed conclusion sheet injected into the
// snapshot response, so the assertions pin the exact sheet shape.

const modelInput =
  "我自己，32岁，手臂不舒服，从昨天开始，变化不大，影响程度3分，没有其他不适，上述危险信号都没有。过敏不知道；是否用过药不确定；病史记不清。这次想整理就医记录。";
const modelFacts = [
  ["consult_for", "self", "我自己", "present"],
  ["age_years", 32, "32岁", "present"],
  ["chief_complaint", "手臂不舒服", "手臂不舒服", "present"],
  ["onset", "昨天", "从昨天开始", "present"],
  ["trajectory", "stable", "变化不大", "present"],
  ["severity", 3, "影响程度3分", "present"],
  ["associated_symptoms", null, "没有其他不适", "absent"],
  ["emergency_screen", null, "上述危险信号都没有", "absent"],
  ["allergies", null, "过敏不知道", "unknown"],
  ["medications_taken", null, "是否用过药不确定", "unknown"],
  ["relevant_history", null, "病史记不清", "unknown"],
  ["patient_goal", "整理就医记录", "这次想整理就医记录", "present"],
];

async function createScriptedModel() {
  const commands = [
    {
      tool: "record_facts",
      arguments: {
        facts: modelFacts.map(([key, value, evidence, assertion]) => ({
          key,
          value,
          evidence,
          assertion,
        })),
      },
    },
    { tool: "review_summary", arguments: {} },
  ];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const command = commands.shift() || {
      tool: "review_summary",
      arguments: {},
    };
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        model: "conclusion-v13-fixture",
        choices: [
          {
            message: { role: "assistant", content: JSON.stringify(command) },
            finish_reason: "stop",
          },
        ],
        usage: { prompt_tokens: 1, completion_tokens: 1 },
      }),
    );
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return {
    endpoint: `http://127.0.0.1:${server.address().port}/v1`,
    async close() {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

async function driveToClosedConclusion(page, request) {
  const model = await createScriptedModel();
  try {
    expect(
      (
        await request.post("/api/settings", {
          data: {
            backend: "compatible",
            base_url: model.endpoint,
            model: "fixture",
            api_key: "local-conclusion-fixture",
          },
        })
      ).ok(),
    ).toBe(true);
    await page.goto("/");
    await expect(page.locator("#connection-status")).toHaveText(
      "本机服务已连接",
    );
    const created = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/sessions"),
    );
    await page.getByRole("button", { name: "开始问诊", exact: true }).click();
    const sessionId = (await (await created).json()).session_id;
    await page.getByRole("textbox", { name: "输入你的描述" }).fill(modelInput);
    await page.getByRole("button", { name: "发送回答" }).click();
    await expect(page.locator(".summary-card.current-summary")).toBeVisible();
    await page
      .getByRole("button", { name: "确认摘要并结束本次问诊", exact: true })
      .click();
    await expect(page.locator(".conclusion-card")).toBeVisible();
    return sessionId;
  } finally {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await model.close();
  }
}

function makeSheet(overrides = {}) {
  return {
    version: 1,
    revision: 1,
    tier: "routine",
    subject_kind: "self",
    disposition: {
      level: "routine_visit",
      text: "请在 48 小时内到线下医疗机构就诊评估。",
      window_hours: 48,
      department_hint: {
        department: "骨科",
        reason: "需要当面检查手臂的活动和压痛情况",
      },
    },
    goal_line: null,
    directions: [],
    directions_abstained: true,
    directions_visible: false,
    directions_mode: "restricted",
    abstain_text:
      "目前的信息还不足以给出可能的方向——这也是建议你留意变化、必要时线下评估的原因之一",
    gaps: [],
    self_care: { do: ["注意休息，避免提重物"], avoid: [], measure: [] },
    return_now_if: ["意识异常或抽搐", "手臂肿胀迅速加重"],
    outlook: "多数情况下会在几天内逐渐好转",
    follow_up: { kind: "routine_checkin", due_in_hours: 48 },
    narrative: {
      framing: "先说结论：这次手臂不舒服，多数情况下不严重。",
      reasoning: "你提到从昨天开始、变化不大，也没有其他不适。",
      severity: "目前属于可以在家先观察的程度，不用急着去医院。",
    },
    department_visible: true,
    generated_by: "conclusion-composer/v1",
    content_versions: {
      differentials: "1.2.1",
      self_care: "1.2",
      departments: "1.0",
    },
    ...overrides,
  };
}

async function interceptSnapshot(page, holder) {
  await page.route("**/api/sessions/*/snapshot", async (route) => {
    const response = await route.fetch();
    const json = await response.json();
    if (json?.state?.conclusion && holder.sheet)
      json.state.conclusion = structuredClone(holder.sheet);
    await route.fulfill({ response, json });
  });
}

async function reopenSession(page, sessionId) {
  await page.reload();
  await expect(page.locator("#connection-status")).toHaveText("本机服务已连接");
  await page.getByRole("button", { name: /会谈记录/ }).click();
  await page.locator(`.history-card[data-session-id="${sessionId}"]`).click();
  await expect(page.locator(".conclusion-card")).toBeVisible({
    timeout: 20000,
  });
}

// Section classes in §13.1 order. Collapse wrappers carry their own
// class and are skipped; inner sections keep document order.
const ORDERED_SECTIONS = [
  "conclusion-narrative",
  "conclusion-worry",
  "conclusion-goal",
  "conclusion-directions",
  "conclusion-disposition",
  "conclusion-visit-notes",
  "conclusion-gaps",
  "conclusion-care",
  "summary-safety",
  "conclusion-outlook",
  "conclusion-closing",
  "conclusion-reading",
];

async function sectionOrder(card) {
  return card.evaluate((root) => {
    const wanted = [
      "conclusion-narrative",
      "conclusion-worry",
      "conclusion-goal",
      "conclusion-directions",
      "conclusion-disposition",
      "conclusion-visit-notes",
      "conclusion-gaps",
      "conclusion-care",
      "summary-safety",
      "conclusion-outlook",
      "conclusion-closing",
      "conclusion-reading",
    ];
    const found = [];
    for (const node of root.querySelectorAll("section")) {
      const hit = wanted.find((name) => node.classList.contains(name));
      if (hit && !found.includes(hit)) found.push(hit);
    }
    return found;
  });
}

const ruleOutDirection = {
  group: "rule_out",
  certainty: "cannot_exclude",
  patient_label: "骨折",
  support_quotes: ["手臂不舒服"],
  against_note: "目前没有畸形或活动受限的表现",
};
const likelyDirection = {
  group: "likely",
  certainty: "more_likely",
  patient_label: "肌肉拉伤",
  support_quotes: ["从昨天开始", "变化不大"],
  against_note: null,
};

function fullSheet(overrides = {}) {
  return makeSheet({
    worry: {
      object: "是不是阑尾炎",
      response:
        "你问到是不是阑尾炎：目前还不能仅凭这次问诊确定这一点，需要当面评估后决定。",
    },
    goal_response:
      "你问到‘平时怎么护眼’：这次问诊的信息还不足以直接回答这一点，需要当面评估后决定；请按结论单的处置建议行动，出现警示信号及时就医。",
    directions: [ruleOutDirection, likelyDirection],
    directions_abstained: false,
    visit_notes: ["手臂不舒服，从昨天开始", "担心是不是阑尾炎"],
    gaps: [{ label: "疼痛的具体位置", impact: "有助于判断是否需要影像检查" }],
    further_reading: {
      citations: [
        {
          title: "肌肉拉伤的家庭护理",
          url: "https://example.org/muscle-strain-care",
          publisher: "示例健康网",
          fetched_at: "2026-10-26",
          tier: "T2",
          quote: "休息与避免提重物有助于恢复。",
        },
      ],
    },
    ...overrides,
  });
}

test("seven-segment order with a single disposition text", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  const holder = { sheet: fullSheet() };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  expect(await sectionOrder(card)).toEqual(ORDERED_SECTIONS);
  // The disposition text is the single source of the destination:
  // exactly one disposition-text node, and the department line lives
  // inside that same disposition section.
  await expect(card.locator(".conclusion-disposition-text")).toHaveCount(1);
  await expect(
    card.locator(".conclusion-disposition .conclusion-department"),
  ).toHaveText("建议科室：骨科 —— 需要当面检查手臂的活动和压痛情况");
});

test("restricted directions render rule_out first and prominent", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  // directions_visible stays false (its full-mode meaning): the
  // restricted rendering is driven by directions_mode alone.
  const holder = { sheet: fullSheet({ directions_visible: false }) };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  const directions = card.locator(".conclusion-directions");
  await expect(directions).toBeVisible();
  // Group titles are fixed, and the exclusion group comes first.
  const groups = directions.locator(".conclusion-direction-group");
  await expect(groups).toHaveText(["需要排除的方向", "更可能的方向"]);
  // The rule_out box keeps its alert class (danger border/background
  // in styles.css) and is not hidden behind a collapsed toggle: the
  // restricted section renders directly, not inside a collapse body.
  const ruleOut = directions.locator(".conclusion-direction.rule_out");
  await expect(ruleOut).toHaveCount(1);
  await expect(ruleOut.locator("strong")).toContainText("目前还不能排除 骨折");
  await expect(ruleOut).toContainText("依据：你提到「手臂不舒服」。");
  await expect(ruleOut).toContainText("目前没有畸形或活动受限的表现。");
  expect(
    await ruleOut.evaluate(
      (node) => node.closest(".conclusion-collapse-body") === null,
    ),
  ).toBe(true);
  await expect(
    directions.locator(".conclusion-direction.likely strong"),
  ).toContainText("更像是 肌肉拉伤");
  // Restricted mode never shows the reasoning line or the abstain
  // text, even when the sheet carries both.
  await expect(card.locator(".conclusion-narrative-reasoning")).toHaveCount(0);
  await expect(card).not.toContainText("目前的信息还不足以给出可能的方向");
  // Fallback: a sheet without directions_mode but with the composer's
  // restricted flag renders the same restricted section.
  holder.sheet = fullSheet({
    directions_visible: false,
    directions_restricted_visible: true,
  });
  delete holder.sheet.directions_mode;
  await reopenSession(page, sessionId);
  await expect(card.locator(".conclusion-directions")).toBeVisible();
  await expect(card.locator(".conclusion-direction-group")).toHaveText([
    "需要排除的方向",
    "更可能的方向",
  ]);
});

test("empty restricted directions render no section at all", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  const holder = {
    sheet: makeSheet({
      directions: [],
      directions_abstained: true,
      directions_mode: "restricted",
    }),
  };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  await expect(card.locator(".conclusion-directions")).toHaveCount(0);
  await expect(card.locator(".conclusion-direction-group")).toHaveCount(0);
  await expect(card).not.toContainText("目前的信息还不足以给出可能的方向");
});

test("department line is gated on department_visible === true", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  const holder = { sheet: makeSheet({ department_visible: false }) };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  await expect(card.locator(".conclusion-disposition-text")).toBeVisible();
  await expect(card.locator(".conclusion-department")).toHaveCount(0);
  // Visible: the line appears inside the disposition section.
  holder.sheet = makeSheet({ department_visible: true });
  await reopenSession(page, sessionId);
  await expect(card.locator(".conclusion-department")).toHaveText(
    "建议科室：骨科 —— 需要当面检查手臂的活动和压痛情况",
    { timeout: 20000 },
  );
  // Missing flag: fail-closed, same absence as false.
  holder.sheet = makeSheet();
  delete holder.sheet.department_visible;
  await reopenSession(page, sessionId);
  await expect(card.locator(".conclusion-department")).toHaveCount(0);
});

test("visit-notes section adapts its title to the subject", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  const holder = {
    sheet: makeSheet({
      visit_notes: ["手臂不舒服，从昨天开始", "担心是不是阑尾炎"],
    }),
  };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  const notes = card.locator(".conclusion-visit-notes");
  await expect(notes.locator("h4")).toHaveText("就诊时请告诉医生");
  await expect(notes.locator("li")).toHaveText([
    "手臂不舒服，从昨天开始",
    "担心是不是阑尾炎",
  ]);
  // Family subject: the title switches to the caregiver wording.
  holder.sheet = makeSheet({
    subject_kind: "family",
    visit_notes: ["手臂不舒服，从昨天开始"],
  });
  await reopenSession(page, sessionId);
  await expect(notes.locator("h4")).toHaveText("就诊时请帮家人告诉医生");
  // Empty list: no section, no empty shell.
  holder.sheet = makeSheet({ visit_notes: [] });
  await reopenSession(page, sessionId);
  await expect(card.locator(".conclusion-visit-notes")).toHaveCount(0);
  // Missing field: same absence.
  holder.sheet = makeSheet();
  await reopenSession(page, sessionId);
  await expect(card.locator(".conclusion-visit-notes")).toHaveCount(0);
});

test("closing line follows the disposition level", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  const holder = { sheet: makeSheet() };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  await expect(card.locator(".conclusion-closing-line")).toHaveText(
    "先别太焦虑，按上面的安排来就好；有变化随时回来问。",
  );
  holder.sheet = makeSheet({
    tier: "urgent",
    disposition: {
      level: "urgent_today",
      text: "建议今天内到线下医疗机构当面评估，不要在家观察等待。",
      window_hours: 24,
      department_hint: null,
    },
  });
  await reopenSession(page, sessionId);
  await expect(card.locator(".conclusion-closing-line")).toHaveText(
    "请按上面的时间尽快就诊，不要等随访。",
  );
});

// Handoff-dialog gating (§13.6, app.js openHandoff copy layer):
// intercept the handoff endpoint with a crafted conclusion, open the
// dialog, and read the .md export — it shares the print sections, so
// it observes exactly what the dialog hands downstream, including the
// copy-layer stripping (a leaked directions list in an off payload
// must still not reach the export).
function makeHandoff(conclusion) {
  return {
    title: "就诊交接摘要",
    facts: [{ label: "主诉", value: "手臂不舒服" }],
    report_items: [],
    needs_confirmation: [],
    limitations: "以上是根据你提供的信息整理的摘要，不替代医生面诊。",
    conclusion,
  };
}

async function exportHandoffMarkdown(page, conclusion) {
  await page.route("**/api/sessions/*/handoff", async (route) => {
    await route.fulfill({ json: { handoff: makeHandoff(conclusion) } });
  });
  await page.getByRole("button", { name: "就诊交接摘要" }).click();
  await expect(page.locator("#handoff-dialog")).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 .md" }).click();
  const download = await downloadPromise;
  const path = await download.path();
  return readFile(path, "utf-8");
}

const endpointDirections = [
  { patient_label: "骨折", group: "rule_out", certainty: "cannot_exclude" },
  { patient_label: "肌肉拉伤", group: "likely", certainty: "more_likely" },
];

test("handoff dialog in restricted mode shows directions, hides reasoning", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  await driveToClosedConclusion(page, request);
  const text = await exportHandoffMarkdown(page, {
    directions_mode: "restricted",
    directions_visible: false,
    department_visible: false,
    disposition: {
      level: "routine_visit",
      text: "请在 48 小时内到线下医疗机构就诊评估。",
      window_hours: 48,
      department_hint: "骨科",
    },
    directions: endpointDirections,
    visit_notes: ["手臂不舒服，从昨天开始", "担心是不是阑尾炎"],
  });
  // Restricted: the composer's directions are visible with the
  // rule_out group prominent; reasoning stays out even though the
  // live sheet carries it; the department line stays out because its
  // flag is false; visit notes render because they have items.
  expect(text).toContain("需要排除的方向：骨折");
  expect(text).toContain("可能方向：肌肉拉伤");
  expect(text).not.toContain("原理：");
  expect(text).not.toContain("建议科室");
  expect(text).toContain("就诊带话清单");
  expect(text).toContain("手臂不舒服，从昨天开始");
});

test("handoff dialog in off mode hides directions even when listed", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  await driveToClosedConclusion(page, request);
  const text = await exportHandoffMarkdown(page, {
    directions_mode: "off",
    directions_visible: false,
    department_visible: true,
    disposition: {
      level: "routine_visit",
      text: "请在 48 小时内到线下医疗机构就诊评估。",
      window_hours: 48,
      department_hint: "骨科",
    },
    // Deliberately leaked list: the copy layer must strip it.
    directions: endpointDirections,
    visit_notes: [],
  });
  expect(text).not.toContain("骨折");
  expect(text).not.toContain("肌肉拉伤");
  expect(text).not.toContain("原理：");
  // The department gate is independent of the direction mode.
  expect(text).toContain("建议科室：骨科");
  expect(text).not.toContain("就诊带话清单");
});

test("handoff dialog in full mode shows directions and reasoning", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  await driveToClosedConclusion(page, request);
  const text = await exportHandoffMarkdown(page, {
    directions_mode: "on",
    directions_visible: true,
    department_visible: true,
    disposition: {
      level: "routine_visit",
      text: "请在 48 小时内到线下医疗机构就诊评估。",
      window_hours: 48,
      department_hint: "骨科",
    },
    directions: endpointDirections,
    visit_notes: ["手臂不舒服，从昨天开始"],
  });
  expect(text).toContain("可能方向：骨折");
  expect(text).toContain("可能方向：肌肉拉伤");
  expect(text).toContain("原理：");
  expect(text).toContain("建议科室：骨科");
  expect(text).toContain("就诊带话清单");
});

test("reminder row focuses the existing follow-up card", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  // Live close (no snapshot interception): the close flow itself
  // auto-schedules the follow-up plan, so this pins §13.5 branch (a)
  // end to end — info line plus a button with a real action.
  await driveToClosedConclusion(page, request);
  const card = page.locator(".conclusion-card");
  const reminder = card.locator(".conclusion-reminder-info");
  await expect(reminder).toHaveText(/我会在 \d+ 小时后来确认/);
  const action = card.locator(".conclusion-reminder-action");
  await expect(action).toHaveText("查看随访安排");
  await action.click();
  const focusedFollowUp = await page.evaluate(
    () => document.activeElement?.closest?.(".followup-card") !== null,
  );
  expect(focusedFollowUp).toBe(true);
});
