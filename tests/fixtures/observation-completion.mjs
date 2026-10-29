import { createServer } from "node:http";
import { nextCommand } from "./model-feedback.mjs";
import { once } from "node:events";
import { expect } from "@playwright/test";
import { degreeSnapshot } from "./visual-answer-drafts.mjs";

export const locationQuestion = "最明显的不适在哪里？可按图选择或跳过。";
export async function createObservationCompletionModel() {
  const fact = (key, value, evidence, assertion = "present") => ({
    key,
    value,
    evidence,
    assertion,
  });
  const commands = [
    {
      tool: "record_facts",
      arguments: {
        facts: [
          fact("consult_for", "self", "我自己"),
          fact("age_years", 32, "32岁"),
          fact("chief_complaint", "头疼", "头疼"),
          fact("emergency_screen", null, "上述危险信号都没有", "absent"),
        ],
      },
    },
    { tool: "show_self_check", arguments: { task_id: "body_location" } },
    {
      tool: "clarify",
      arguments: {
        topic: "symptom_location",
        message: locationQuestion,
        answer_fields: ["region"],
      },
    },
    {
      tool: "record_facts",
      arguments: {
        facts: [fact("medications_taken", "昨晚用过药物甲", "昨晚用过药物甲")],
      },
    },
    {
      tool: "propose_self_check_result",
      arguments: {
        task_id: "body_location",
        option: "head",
        evidence: "右太阳穴附近疼",
      },
    },
    null,
    { tool: "ask_question", arguments: { question_id: "onset" } },
  ];
  const requests = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    const command = nextCommand(body, commands);
    response.writeHead(command ? 200 : command === null ? 503 : 500, {
      "Content-Type": "application/json",
    });
    response.end(
      JSON.stringify(
        command
          ? {
              choices: [
                {
                  message: {
                    role: "assistant",
                    content: JSON.stringify(command),
                  },
                  finish_reason: "stop",
                },
              ],
            }
          : { error: "Synthetic unavailable service" },
      ),
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

export async function verifyObservationCompletion(page, model, { shot } = {}) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  const send = async (text) => {
    await input.fill(text);
    await page.getByRole("button", { name: "发送回答" }).click();
    await expect(page.locator("#send-button")).toBeEnabled();
  };
  await send("我自己32岁，头疼；上述危险信号都没有。");
  const original = await degreeSnapshot(page, id);
  const originalTask = original.events
    .filter((e) => e.event_type === "turn.agent_reply")
    .at(-1).payload.reply.task;
  await page.locator(".location-step-guide > summary").click();
  await page
    .locator("#active-task")
    .getByRole("button", { name: "下一步", exact: true })
    .click();
  await send("可能在右太阳穴附近，具体位置还不确定。");
  await expect(page.locator("#active-task h3")).toHaveText(locationQuestion);
  const clarified = await degreeSnapshot(page, id);
  const task = clarified.events
    .filter((e) => e.event_type === "turn.agent_reply")
    .at(-1).payload.reply.task;
  expect(task.interaction_id).toBe(originalTask.interaction_id);
  expect(clarified.state.facts.symptom_location).toBeUndefined();
  expect(clarified.state.facts.associated_symptoms).toBeUndefined();
  expect(clarified.state.active_task).toBe("body_location");
  await expect(page.locator("#active-task .step-progress > span")).toHaveText(
    "2 / 2",
  );
  const rectangles = await page.evaluate(() => {
    const pane = document.querySelector("#active-task").getBoundingClientRect();
    return [
      "#active-task h3",
      ".body-map-canvas",
      ".body-map-options",
      ".map-confirm",
      "#message-input",
      "#urgent-help",
    ].map((selector) => {
      const rect = document.querySelector(selector).getBoundingClientRect();
      return {
        selector,
        visible:
          rect.top >= 0 &&
          rect.bottom <= innerHeight &&
          (["#message-input", "#urgent-help"].includes(selector) ||
            (rect.top >= pane.top && rect.bottom <= pane.bottom)),
      };
    });
  });
  expect(rectangles.filter((r) => !r.visible)).toEqual([]);
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  const text = "右太阳穴附近疼；昨晚用过药物甲。";
  await send(text);
  await expect(
    page.getByRole("button", { name: "核对并记录：头/面部", exact: true }),
  ).toBeEnabled();
  await expect(page.locator("#active-task h3")).toHaveText(
    "指出最明显的不适部位",
  );
  const draft = "另外一段尚未发送的补充。";
  await input.fill(draft);
  await page
    .getByRole("button", { name: "核对并记录：头/面部", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "重试这一轮", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("这次反馈已处理。后续对话暂时不可用，重试即可继续。", {
      exact: true,
    }),
  ).toBeVisible();
  const failed = await degreeSnapshot(page, id);
  const result = failed.state.facts.symptom_location;
  expect(result.value).toBe("头/面部");
  const firstContext = JSON.parse(model.requests[5].messages[1].content);
  expect(firstContext.observation_completion.fact_id).toBe(result.fact_id);
  expect(firstContext.observation_completion.result_is_current).toBe(true);
  expect(firstContext.latest_user_text).toBe(text);
  await page.getByRole("button", { name: "重试这一轮", exact: true }).click();
  await expect(page.locator("#send-button")).toBeEnabled();
  await expect(page.locator("#active-question h2")).toBeVisible();
  const completed = await degreeSnapshot(page, id);
  expect(completed.state.facts.symptom_location.fact_id).toBe(result.fact_id);
  expect(completed.state.facts.associated_symptoms).toBeUndefined();
  expect(completed.state.facts.symptom_location.evidence).toBe(
    "右太阳穴附近疼",
  );
  const retriedContext = JSON.parse(model.requests[6].messages[1].content);
  expect(retriedContext.observation_completion).toEqual(
    firstContext.observation_completion,
  );
  expect(retriedContext.latest_user_text).toBe(text);
  expect(
    completed.events.filter(
      (e) =>
        e.event_type === "fact.recorded" &&
        e.payload.key === "symptom_location",
    ),
  ).toHaveLength(1);
  await expect(input).toHaveValue(draft);
  expect(model.requests).toHaveLength(7);
  expect(errors).toEqual([]);
}
