import { expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import { nextCommand } from "./model-feedback.mjs";

const raw =
  "我自己，32岁。手臂不舒服，从昨天开始，变化不大，影响程度3分。没有其他不适；上述危险信号都没有。昨晚用过药物甲，用量不清楚。没有过敏；没有相关既往病史。这次想整理就医记录。";
const rows = [
  ["consult_for", "self", "我自己", "present"],
  ["age_years", 32, "32岁", "present"],
  ["chief_complaint", "手臂不舒服", "手臂不舒服", "present"],
  ["onset", "昨天", "从昨天开始", "present"],
  ["trajectory", "stable", "变化不大", "present"],
  ["severity", 3, "影响程度3分", "present"],
  ["associated_symptoms", null, "没有其他不适", "absent"],
  ["emergency_screen", null, "上述危险信号都没有", "absent"],
  ["medications_taken", "用过药物甲", "昨晚用过药物甲", "present"],
  ["allergies", null, "没有过敏", "absent"],
  ["relevant_history", null, "没有相关既往病史", "absent"],
  ["patient_goal", "整理就医记录", "想整理就医记录", "present"],
];

export async function createPresentProvenanceModel() {
  const requests = [];
  const commands = [
    {
      tool: "record_facts",
      arguments: {
        facts: rows.map(([key, value, evidence, assertion]) => ({
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
    let body = "";
    for await (const chunk of request) body += chunk;
    const payload = JSON.parse(body);
    requests.push(payload);
    const command = nextCommand(payload, commands);
    if (!command) {
      response.writeHead(500);
      response.end("Unexpected local model call");
      return;
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        model: "local-provenance",
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
    requests,
    async close() {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

export async function verifyPresentProvenance(page, model, screenshot) {
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  await page.getByRole("textbox", { name: "输入你的描述" }).fill(raw);
  await page.getByRole("button", { name: "发送回答" }).click();
  const current = () => page.locator(".summary-card.current-summary");
  const medication = () =>
    current().locator(".summary-item[data-fact-key='medications_taken']");
  await expect(medication()).toBeVisible();
  await expect(medication().locator(".summary-evidence")).toBeHidden();
  const snapshot = () =>
    page.evaluate(
      async (sessionId) =>
        (await fetch(`/api/sessions/${sessionId}/snapshot`)).json(),
      id,
    );
  const before = await snapshot();
  expect(before.state.facts.medications_taken.value).toBe("用过药物甲");
  expect(before.state.facts.medications_taken.assertion).toBe("present");
  expect(model.requests).toHaveLength(2);
  async function openSource() {
    const provenance = medication().locator(".summary-provenance");
    const toggle = provenance.locator(":scope > summary");
    expect((await toggle.boundingBox()).height).toBeGreaterThanOrEqual(44);
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(provenance.locator(".summary-evidence")).toHaveText(
      "昨晚用过药物甲",
    );
    await expect(provenance).toContainText("质量未评价");
    const context = provenance.locator(".summary-source-context");
    await expect(context.locator("blockquote")).toBeHidden();
    await context.locator("summary").click();
    await expect(context.locator("blockquote")).toBeVisible();
    await expect(context.locator("blockquote")).toHaveText(raw);
    await expect(context).toContainText("请以各项最新记录为准");
  }
  await openSource();
  expect((await snapshot()).state.revision).toBe(before.state.revision);
  expect(model.requests).toHaveLength(2);
  await current()
    .locator(".summary-item[data-fact-key='age_years']")
    .getByRole("button", { name: "更正", exact: true })
    .click();
  await page.getByLabel("更正「年龄」").fill("40岁");
  await page.getByRole("button", { name: "保存并重新核对" }).click();
  await expect(page.locator("#edit-dialog")).toBeHidden();
  await expect(
    current()
      .locator(".summary-item[data-fact-key='age_years'] strong")
      .first(),
  ).toHaveText("40 岁");
  await openSource();
  expect((await snapshot()).state.facts.age_years.value).toBe(40);
  expect(model.requests).toHaveLength(2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  if (screenshot) {
    await medication()
      .locator(".source-evidence-match")
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: screenshot, fullPage: true });
  }
  await medication().getByRole("button", { name: "更正", exact: true }).click();
  await page.getByLabel("更正「用药与处理」").fill("今晚用过药物乙");
  await page.getByRole("button", { name: "保存并重新核对" }).click();
  await expect(page.locator("#edit-dialog")).toBeHidden();
  await expect(medication()).not.toContainText("药物甲");
  await medication().locator(".summary-provenance > summary").click();
  await expect(medication().locator(".summary-evidence")).toHaveText(
    "今晚用过药物乙",
  );
  await expect(medication().locator(".summary-source-context")).toHaveCount(0);
  const after = await snapshot();
  expect(
    after.state.history
      .filter((fact) => fact.key === "medications_taken")
      .map((fact) => fact.evidence),
  ).toEqual(["昨晚用过药物甲", "今晚用过药物乙"]);
  expect(model.requests).toHaveLength(2);
  await page
    .getByRole("button", { name: "确认摘要并结束本次问诊", exact: true })
    .click();
  await expect(page.locator("#composer")).toBeHidden();
}
