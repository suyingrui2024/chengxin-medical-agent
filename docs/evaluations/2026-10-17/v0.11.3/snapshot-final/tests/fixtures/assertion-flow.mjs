import { expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";

const input =
  "我自己，32岁，手臂不舒服，从昨天开始，变化不大，影响程度3分，没有其他不适，上述危险信号都没有。过敏不知道；是否用过药不确定；病史记不清。这次想整理就医记录。";
const history = [
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

export async function createAssertionModel() {
  const requests = [];
  const commands = [false, true].map((corrected) => ({
    tool: "record_facts",
    arguments: {
      facts: history.map(([key, value, evidence, assertion]) => ({
        key,
        value,
        evidence,
        assertion: !corrected && assertion === "unknown" ? "absent" : assertion,
      })),
    },
  }));
  commands.push({ tool: "review_summary", arguments: {} });
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    requests.push(JSON.parse(raw));
    const command = commands.shift();
    if (!command) {
      response.writeHead(500);
      response.end("Unexpected fixture call");
      return;
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        model: "local-assertion-fixture",
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

export async function verifyAssertionFlow(page, model) {
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  await page.getByRole("textbox", { name: "输入你的描述" }).fill(input);
  await page.getByRole("button", { name: "发送回答" }).click();
  const current = () => page.locator(".summary-card.current-summary");
  await expect(current()).toBeVisible();
  await expect(page.locator("#messages")).not.toContainText("unknown/null");
  await expect(current().locator(".unknown-report")).toHaveCount(3);
  await expect(current().locator(".unknown-items")).toContainText("过敏不知道");
  expect(model.requests).toHaveLength(3);
  const observed = JSON.parse(model.requests[1].messages.at(-1).content);
  expect(observed.tool_result.ok).toBe(false);
  expect(observed.tool_result.error).toContain("unknown/null");
  const snapshot = () =>
    page.evaluate(
      async (sessionId) =>
        (await fetch(`/api/sessions/${sessionId}/snapshot`)).json(),
      id,
    );
  let data = await snapshot();
  for (const key of ["allergies", "medications_taken", "relevant_history"]) {
    expect(data.state.facts[key].assertion).toBe("unknown");
    expect(data.state.facts[key].value).toBeNull();
    expect(data.state.history.filter((item) => item.key === key)).toHaveLength(
      1,
    );
  }
  expect(data.state.history).toHaveLength(12);
  expect(data.state.facts.age_years.value).toBe(32);
  expect(data.state.facts.associated_symptoms.assertion).toBe("absent");

  await current()
    .locator(".unknown-report[data-fact-key='allergies']")
    .getByRole("button", { name: "补充 / 更正", exact: true })
    .click();
  const editor = page.getByLabel("更正「过敏信息」");
  await expect(editor).toHaveValue("过敏不知道");
  await editor.fill("好像没有过敏");
  await page.getByRole("button", { name: "保存并重新核对" }).click();
  await expect(page.locator("#edit-dialog")).toBeHidden();
  await expect(
    current()
      .locator(".unknown-report[data-fact-key='allergies'] blockquote")
      .first(),
  ).toHaveText("好像没有过敏");
  data = await snapshot();
  expect(data.state.facts.allergies.assertion).toBe("unknown");
  expect(model.requests).toHaveLength(3);
  const recorded = data.events.filter(
    (event) =>
      event.event_type === "fact.recorded" &&
      ["allergies", "medications_taken", "relevant_history"].includes(
        event.payload.key,
      ),
  );
  expect(recorded.every((event) => event.payload.assertion === "unknown")).toBe(
    true,
  );
  await page.getByRole("button", { name: "摘要准确", exact: true }).click();
  await expect(page.locator("#composer")).toBeHidden();
  expect(model.requests).toHaveLength(3);
}
