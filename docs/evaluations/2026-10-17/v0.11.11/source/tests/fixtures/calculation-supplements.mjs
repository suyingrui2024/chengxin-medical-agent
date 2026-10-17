import { createServer } from "node:http";
import { once } from "node:events";
import { expect } from "@playwright/test";

const text = "请把1.25kg换算成g；昨晚用过药物甲。";
const earlier = `我32岁；${text}`;
const fact = (key, value, evidence, correction = false) => ({
  key,
  value,
  evidence,
  assertion: "present",
  correction,
});
const record = (facts) => ({ tool: "record_facts", arguments: { facts } });
const medication = fact(
  "medications_taken",
  "昨晚用过药物甲",
  "昨晚用过药物甲",
);
const ask = (id) => ({ tool: "ask_question", arguments: { question_id: id } });
const calculate = (evidence) => ({
  tool: "run_capability",
  arguments: {
    id: "calculate.units",
    input: { value: "1.25", from_unit: "kg", to_unit: "g" },
    evidence,
  },
});

export async function createCalculationSupplementsModel() {
  const requests = [];
  const commands = [
    calculate(text),
    null,
    record([medication]),
    ask("age"),
    calculate(earlier),
    record([fact("age_years", 34, "34岁")]),
    ask("chief_complaint"),
    record([fact("age_years", 32, "32岁", true), medication]),
    record([medication]),
    ask("chief_complaint"),
  ];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    requests.push(JSON.parse(raw));
    const command = commands.shift();
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
          : { error: "Synthetic service failure" },
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

export async function verifyCalculationSupplements(
  page,
  model,
  { shots, mobile = false } = {},
) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const send = async (input) => {
    const response = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        /\/api\/sessions\/[^/]+\/messages$/.test(r.url()),
    );
    await page.getByRole("textbox", { name: "输入你的描述" }).fill(input);
    await page.getByRole("button", { name: "发送回答" }).click();
    return (await response).json();
  };
  const snapshot = (id) =>
    page.evaluate(
      async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
      id,
    );
  const initial = await send(text),
    id = initial.state.session_id;
  const source = initial.reply.calculation_followup.source_turn_id;
  const form = page.locator("#active-question .calculation-form");
  await expect(form).toBeVisible();
  await expect(
    page.locator("#active-question .interaction-followup summary"),
  ).toHaveText("还有同轮补充");
  await form.getByRole("button", { name: "核对后计算", exact: true }).click();
  await expect(
    page.locator("#active-question .patient-calculation-result h3"),
  ).toHaveText("1250 g");
  const continueButton = page.getByRole("button", {
    name: "继续处理补充",
    exact: true,
  });
  await expect(continueButton).toBeVisible();
  const bounds = await continueButton.boundingBox(),
    area = await page.locator("#active-question").boundingBox();
  expect(bounds.y).toBeGreaterThanOrEqual(area.y);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(area.y + area.height);
  expect(model.requests).toHaveLength(1);
  await page.reload();
  await page.locator("#recent-sessions .recent-item").first().click();
  await expect(continueButton).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}-result.png` });
  await continueButton.click();
  await expect(
    page.getByRole("button", { name: "重试这一轮", exact: true }),
  ).toBeVisible();
  await expect(page.locator("#view-consultation h1")).toHaveText(
    "补充信息待处理",
  );
  await expect(page.locator("#composer-context")).toHaveText(
    "补充原话已保留，可以重试。",
  );
  const failed = await snapshot(id);
  expect(failed.state.calculations).toHaveLength(1);
  const failedReply = failed.events
    .filter((event) => event.event_type === "turn.agent_reply")
    .at(-1).payload.reply;
  expect(failedReply.calculation_followup.status).toBe("pending");
  const pending = page
    .locator('#messages .interaction-followup[data-status="pending"]')
    .last();
  await pending.locator("summary").click();
  await expect(pending.locator("blockquote")).toHaveText(text);
  if (shots) await page.screenshot({ path: `${shots}-retry.png` });
  await page.getByRole("button", { name: "重试这一轮", exact: true }).click();
  await expect(
    page.locator("#active-question .interaction-followup"),
  ).toContainText("补充已带入问诊");
  const recovered = await snapshot(id);
  expect(recovered.state.calculations).toHaveLength(1);
  expect(recovered.state.facts.medications_taken.turn_id).toBe(source);
  expect(recovered.state.facts.medications_taken.evidence).toBe(
    "昨晚用过药物甲",
  );
  expect(model.requests).toHaveLength(4);
  await page.locator("#new-session").click();
  const second = await send(earlier),
    other = second.state.session_id;
  const oldAction = second.reply.action_id;
  const updated = await send("先不换算了，我34岁。");
  expect(updated.state.facts.age_years.value).toBe(34);
  const ageId = updated.state.facts.age_years.fact_id;
  const process = page.locator("#active-question .followup-process");
  await expect(process).toBeVisible();
  const stale = await page.evaluate(
    async ({ other, oldAction, id }) =>
      (
        await fetch(`/api/sessions/${other}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: "处理旧页面补充",
            choice_value: `process_calculation_supplement:${id}`,
            expected_action_id: oldAction,
          }),
        })
      ).status,
    { other, oldAction, id: second.reply.calculation_followup.id },
  );
  expect(stale).toBe(409);
  expect(model.requests).toHaveLength(7);
  if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  const processBounds = await process.boundingBox();
  expect(processBounds.y + processBounds.height).toBeLessThanOrEqual(
    await page.evaluate(() => innerHeight),
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  if (shots) await page.screenshot({ path: `${shots}-pending.png` });
  await process.click();
  await expect(
    page.locator("#active-question .interaction-followup"),
  ).toContainText("补充已带入问诊");
  const final = await snapshot(other);
  expect(final.state.facts.age_years.value).toBe(34);
  expect(final.state.facts.age_years.fact_id).toBe(ageId);
  expect(final.state.facts.medications_taken.turn_id).toBe(
    second.reply.calculation_followup.source_turn_id,
  );
  expect(final.state.calculations).toEqual([]);
  expect(
    final.events.some(
      (event) =>
        event.event_type === "tool.completed" &&
        JSON.stringify(event.payload).includes("不能覆盖随后"),
    ),
  ).toBe(true);
  expect(model.requests).toHaveLength(10);
  expect(errors).toEqual([]);
}
