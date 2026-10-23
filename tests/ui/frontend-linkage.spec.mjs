import { test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";

// Frontend linkage regressions: nav badge hydration, focus hand-off on
// view switches, friendly non-JSON API errors, follow-up draft display,
// and the post-close education refresh of the conclusion card.

test("records badge shows the real count on the first screen", async ({
  page,
}) => {
  await page.route("**/api/patient/timeline", (route) =>
    route.fulfill({
      json: {
        entries: [{ entry_id: "a" }, { entry_id: "b" }, { entry_id: "c" }],
      },
    }),
  );
  await page.goto("/");
  await expect(page.locator("#connection-status")).toHaveText("本机服务已连接");
  // Still on the consultation view: the badge must not wait for the
  // records view to be opened before it stops claiming zero.
  await expect(page.locator("#view-consultation")).toBeVisible();
  await expect(page.locator("#records-count")).toHaveText("3");
});

test("view navigation moves focus to the incoming view heading", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("#connection-status")).toHaveText("本机服务已连接");
  await page.getByRole("button", { name: /会谈记录/ }).click();
  await expect(page.locator("#page-title")).toHaveText("会谈记录");
  await expect(page.locator("#view-history h1")).toBeFocused();
  await page.getByRole("button", { name: /健康档案/ }).click();
  await expect(page.locator("#page-title")).toHaveText("健康档案");
  await expect(page.locator("#view-records h1")).toBeFocused();
});

test("a non-JSON API response surfaces a friendly Chinese error", async ({
  page,
}) => {
  await page.route("**/api/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "<html>proxy error</html>",
    }),
  );
  await page.goto("/");
  const toast = page.locator("#toast");
  await expect(toast).toContainText("服务返回了无法识别的响应（状态码 200）");
  await expect(toast).not.toContainText("Unexpected");
});

test("follow-up cards render a stored draft read-only", async ({ page }) => {
  const plan = {
    kind: "routine_checkin",
    reason: "复查恢复情况",
    due_at: "2999-01-01T09:00:00+08:00",
    status: "scheduled",
  };
  await page.route("**/api/follow-ups", (route) =>
    route.fulfill({
      json: {
        follow_ups: [
          {
            session_id: "session-with-draft",
            title: "手臂不舒服",
            plan,
            draft: {
              priority: "routine",
              draft_message: "您好，想了解一下您手臂的恢复情况。",
            },
          },
          {
            session_id: "session-without-draft",
            title: "咳嗽发烧两天",
            plan,
          },
        ],
      },
    }),
  );
  await page.goto("/");
  await expect(page.locator("#connection-status")).toHaveText("本机服务已连接");
  await page.getByRole("button", { name: /会谈记录/ }).click();
  const cards = page.locator(".followup-card");
  await expect(cards).toHaveCount(2);
  const draft = cards.first().locator(".followup-draft");
  await expect(draft).toContainText("草稿，待确认");
  await expect(draft).toContainText("您好，想了解一下您手臂的恢复情况。");
  // Read-only means read-only: no action of any kind inside the draft.
  await expect(draft.locator("button, a, input, textarea")).toHaveCount(0);
  await expect(cards.nth(1).locator(".followup-draft")).toHaveCount(0);
});

// -- Scripted-model consultation that closes with a conclusion sheet --

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
        model: "frontend-linkage-fixture",
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

const citation = {
  citation_id: "cit-linkage-1",
  title: "上肢不适自我护理要点",
  url: "https://example.org/arm-care",
  publisher: "示例医学机构",
  published_at: "2024-05-01",
  fetched_at: "2026-10-24",
  tier: "T1",
  quote: "出现手臂不适时应注意休息并观察变化。",
};

function injectReading(json) {
  const conclusion = json?.state?.conclusion;
  if (conclusion)
    conclusion.further_reading = {
      status: "ready",
      sheet_revision: conclusion.revision,
      sources_version: "linkage-test",
      citations: [citation],
    };
  return json;
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
            api_key: "local-linkage-fixture",
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

test("education completion refreshes the conclusion card in place", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  let enrich = false;
  await page.route("**/api/sessions/*/events", async (route) => {
    const response = await route.fetch();
    const json = await response.json();
    if (enrich)
      json.events.push({
        event_type: "web.education_completed",
        payload: { citation_count: 1, duration_ms: 5 },
      });
    await route.fulfill({ response, json });
  });
  await page.route("**/api/sessions/*/snapshot", async (route) => {
    const response = await route.fetch();
    const json = await response.json();
    await route.fulfill({
      response,
      json: enrich ? injectReading(json) : json,
    });
  });
  await driveToClosedConclusion(page, request);
  await expect(page.locator(".conclusion-reading")).toHaveCount(0);
  // The session is closed and its conclusion has no further_reading, so
  // the bounded education watch keeps polling; once the completion
  // event lands, the frozen card must gain the reading section.
  enrich = true;
  const reading = page.locator(".conclusion-card .conclusion-reading");
  await expect(reading).toBeVisible({ timeout: 20000 });
  await expect(reading).toContainText("上肢不适自我护理要点");
});

test("resuming a closed session overlays the enriched conclusion", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  const sessionId = await driveToClosedConclusion(page, request);
  await page.route("**/api/sessions/*/snapshot", async (route) => {
    const response = await route.fetch();
    const json = await response.json();
    await route.fulfill({ response, json: injectReading(json) });
  });
  await page.reload();
  await expect(page.locator("#connection-status")).toHaveText("本机服务已连接");
  await page.getByRole("button", { name: /会谈记录/ }).click();
  await page.locator(`.history-card[data-session-id="${sessionId}"]`).click();
  // The replayed replies carry the pre-enrichment sheet; the card must
  // still end up showing the reading section from the stored state.
  const reading = page.locator(".conclusion-card .conclusion-reading");
  await expect(reading).toBeVisible({ timeout: 20000 });
  await expect(reading).toContainText("上肢不适自我护理要点");
});
