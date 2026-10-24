import { test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";

// Conclusion card P1 regressions: the narrative block (framing /
// severity with level dot / reasoning), the department hint gated by
// department_visible, and the three disposition level classes. Each
// test drives a real scripted-model consultation to a close, then
// reopens the stored session with a constructed conclusion sheet
// injected into the snapshot response (same interception style as
// frontend-linkage.spec.mjs), so the assertions pin the exact sheet
// shape the backend lane produces.

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
        model: "conclusion-card-fixture",
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
      level: "self_care",
      text: "可以先在家观察；如果提前加重或出现下方任何警示信号，请及时就医。",
      window_hours: null,
      department_hint: {
        department: "骨科",
        reason: "需要当面检查手臂的活动和压痛情况",
      },
    },
    goal_line: null,
    directions: [],
    directions_abstained: true,
    directions_visible: false,
    abstain_text:
      "目前的信息还不足以给出可能的方向——这也是建议你留意变化、必要时线下评估的原因之一",
    gaps: [],
    self_care: { do: ["注意休息，避免提重物"], avoid: [], measure: [] },
    return_now_if: ["意识异常或抽搐", "手臂肿胀迅速加重"],
    outlook: null,
    follow_up: null,
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

// Intercept snapshot responses; the stored conclusion is replaced by
// holder.sheet (when set) before fulfilling. Register once per test.
async function interceptSnapshot(page, holder) {
  await page.route("**/api/sessions/*/snapshot", async (route) => {
    const response = await route.fetch();
    const json = await response.json();
    if (json?.state?.conclusion && holder.sheet)
      json.state.conclusion = structuredClone(holder.sheet);
    await route.fulfill({ response, json });
  });
}

// Reload and reopen the stored session from the history list so its
// conclusion card is rendered from the (intercepted) snapshot.
async function reopenSession(page, sessionId) {
  await page.reload();
  await expect(page.locator("#connection-status")).toHaveText("本机服务已连接");
  await page.getByRole("button", { name: /会谈记录/ }).click();
  await page.locator(`.history-card[data-session-id="${sessionId}"]`).click();
  await expect(page.locator(".conclusion-card")).toBeVisible({
    timeout: 20000,
  });
}

test("narrative block and visible department render on the conclusion card", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  const holder = { sheet: makeSheet({ directions_visible: true }) };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  // Department lives inside the disposition section, gated visible.
  const disposition = card.locator(".conclusion-disposition");
  await expect(disposition).toHaveClass(/self_care/);
  await expect(disposition.locator(".conclusion-department")).toHaveText(
    "建议科室：骨科 —— 需要当面检查手臂的活动和压痛情况",
  );
  // Narrative follows with framing first, then severity with its dot.
  const narrative = card.locator(".conclusion-narrative");
  await expect(narrative).toBeVisible();
  await expect(narrative.locator(".conclusion-narrative-framing")).toHaveText(
    "先说结论：这次手臂不舒服，多数情况下不严重。",
  );
  const severity = narrative.locator(".conclusion-narrative-severity");
  await expect(severity).toHaveClass(/self_care/);
  await expect(severity.locator(".conclusion-level-dot")).toHaveClass(
    /self_care/,
  );
  await expect(severity).toContainText("目前属于可以在家先观察的程度");
  await expect(
    narrative.locator(".conclusion-narrative-reasoning-label"),
  ).toHaveText("为什么这样判断");
  await expect(
    narrative.locator(".conclusion-narrative-reasoning-text"),
  ).toContainText("从昨天开始、变化不大");
});

test("shadow department and absent narrative stay hidden", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  const holder = {
    sheet: makeSheet({ narrative: null, department_visible: false }),
  };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  // The card itself still renders; only the gated blocks are absent.
  await expect(card.locator(".conclusion-disposition-text")).toBeVisible();
  await expect(card.locator(".conclusion-department")).toHaveCount(0);
  await expect(card.locator(".conclusion-narrative")).toHaveCount(0);
});

test("disposition level classes follow the sheet level", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  const holder = { sheet: null };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  const variants = [
    {
      tier: "routine",
      level: "routine_visit",
      text: "可以在家先观察；请在 48 小时内到线下医疗机构就诊评估。",
      window_hours: 48,
    },
    {
      tier: "urgent",
      level: "urgent_today",
      text: "建议今天内到线下医疗机构当面评估，不要在家观察等待。",
      window_hours: 24,
    },
  ];
  for (const variant of variants) {
    holder.sheet = makeSheet({
      tier: variant.tier,
      disposition: {
        level: variant.level,
        text: variant.text,
        window_hours: variant.window_hours,
        department_hint: null,
      },
    });
    await reopenSession(page, sessionId);
    const disposition = card.locator(".conclusion-disposition");
    await expect(disposition).toHaveClass(new RegExp(variant.level), {
      timeout: 20000,
    });
    await expect(
      card.locator(`.conclusion-level-dot.${variant.level}`),
    ).toHaveCount(1);
    await expect(
      card.locator(`.conclusion-narrative-severity.${variant.level}`),
    ).toHaveCount(1);
  }
});

test("narrative reasoning is gated on directions visibility", async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const sessionId = await driveToClosedConclusion(page, request);
  // Shadow: directions hidden, so the reasoning paragraph (which quotes
  // direction labels) must not reach the DOM; framing/severity remain.
  const holder = { sheet: makeSheet({ directions_visible: false }) };
  await interceptSnapshot(page, holder);
  await reopenSession(page, sessionId);
  const card = page.locator(".conclusion-card");
  const narrative = card.locator(".conclusion-narrative");
  await expect(narrative).toBeVisible();
  await expect(narrative.locator(".conclusion-narrative-framing")).toHaveText(
    "先说结论：这次手臂不舒服，多数情况下不严重。",
  );
  await expect(
    narrative.locator(".conclusion-narrative-severity"),
  ).toContainText("目前属于可以在家先观察的程度");
  await expect(
    narrative.locator(".conclusion-narrative-reasoning"),
  ).toHaveCount(0);
  await expect(narrative).not.toContainText("为什么这样判断");
  // Visible: the same sheet with directions_visible flips reasoning in.
  holder.sheet = makeSheet({ directions_visible: true });
  await reopenSession(page, sessionId);
  await expect(
    narrative.locator(".conclusion-narrative-reasoning-label"),
  ).toHaveText("为什么这样判断", { timeout: 20000 });
  await expect(
    narrative.locator(".conclusion-narrative-reasoning-text"),
  ).toContainText("从昨天开始、变化不大");
});
