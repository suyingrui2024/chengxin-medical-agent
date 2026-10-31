import { createServer } from "node:http";
import { once } from "node:events";
import { expect } from "@playwright/test";
import { nextCommand } from "./model-feedback.mjs";

// Sex is stated up front on purpose. The engine orders questions by
// hypothesis (d1568d6), and with sex unknown it ranks `sex` above
// `trajectory` — so a scripted `ask_question(trajectory)` is rejected and
// every later command lands in the wrong turn. Recording it in the
// opening keeps the script in phase without weakening the check: if the
// ordering changes again, this test fails instead of silently desyncing.
export const opening =
  "我自己32岁，男，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。";
const details =
  "总体变化不大；不适程度3分；没有其他不适；没有气促，能完整说话；干咳，没有咳血；能正常喝水吃饭，排尿正常；没有用药；过敏不知道；没有相关既往病史；想整理信息去就医。";
const fact = (key, value, evidence, assertion = "present") => ({
  key,
  value,
  evidence,
  assertion,
});
const command = (tool, args = {}) => ({ tool, arguments: args });

export async function createRepeatedObservationModel(extraCommands = []) {
  const requests = [];
  const commands = [
    command("record_facts", {
      facts: [
        fact("consult_for", "self", "我自己"),
        fact("sex", "male", "男"),
        fact("age_years", 32, "32岁"),
        fact("chief_complaint", "咳嗽发烧", "咳嗽发烧"),
        fact("onset", "两天", "两天"),
        fact("emergency_screen", null, "上述危险信号都没有", "absent"),
      ],
    }),
    command("ask_question", { question_id: "trajectory" }),
    command("record_facts", {
      facts: [
        fact("trajectory", "stable", "变化不大"),
        fact("severity", 3, "不适程度3分"),
        fact("associated_symptoms", null, "没有其他不适", "absent"),
        fact("resp_breathing", "没有气促，能完整说话", "没有气促，能完整说话"),
        fact("resp_cough_details", "干咳，没有咳血", "干咳，没有咳血"),
        fact(
          "resp_intake",
          "能正常喝水吃饭，排尿正常",
          "能正常喝水吃饭，排尿正常",
        ),
        fact("medications_taken", null, "没有用药", "absent"),
        fact("allergies", null, "过敏不知道", "unknown"),
        fact("relevant_history", null, "没有相关既往病史", "absent"),
        fact("patient_goal", "整理信息去就医", "想整理信息去就医"),
      ],
    }),
    command("review_summary"),
    ...extraCommands,
  ];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    // The engine's ordering check rejects a proposal and names the
    // question it wants first; nextCommand obeys that without advancing
    // the script. The context lists `questions` in content order, not in
    // the ranker's order, so the script cannot predict the preference.
    const next = nextCommand(body, commands);
    response.writeHead(next ? 200 : 500, {
      "Content-Type": "application/json",
    });
    response.end(
      JSON.stringify(
        next
          ? {
              choices: [
                {
                  message: { role: "assistant", content: JSON.stringify(next) },
                  finish_reason: "stop",
                },
              ],
            }
          : { error: "Unexpected model request" },
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

export async function sendPatientText(page, text) {
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  if (!(await input.isVisible())) {
    const guide = page.locator("#active-task .illustrated-task");
    // The compact measurement guide exposes the composer only after the
    // patient has reviewed its preparation and placement steps.
    await expect(guide).toHaveAttribute("data-guide-step", "1");
    await guide.getByRole("button", { name: "下一步", exact: true }).click();
    await expect(guide).toHaveAttribute("data-guide-step", "2");
    await guide.getByRole("button", { name: "下一步", exact: true }).click();
  }
  await expect(input).toBeVisible();
  await input.fill(text);
  await page.getByRole("button", { name: "发送回答" }).click();
}

export async function verifyRepeatedObservations(
  page,
  model,
  screenshotPrefix,
) {
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  const send = (text) => sendPatientText(page, text);
  const current = () => page.locator(".summary-card.current-summary");
  const series = () => current().locator(".observation-card");
  await send(opening);
  await expect(page.locator("#active-task .structured-task")).toBeVisible();
  await send("腋温38.25℃");
  await expect(page.locator("#active-question")).toBeVisible();
  await send(details);
  await expect(series().locator(".observation-value")).toHaveText("38.25 ℃");
  // One call for the opening, two for the temperature (the first
  // proposal is rejected by the ordering check and re-issued), two for
  // the details (record, then summarise). The extra call is the cost of
  // the context listing `questions` in content order rather than the
  // ranker's order — the model has to be told which question wins.
  expect(model.requests).toHaveLength(5);
  const oldSnapshot = await page.evaluate(
    async (sessionId) =>
      (await fetch(`/api/sessions/${sessionId}/snapshot`)).json(),
    id,
  );
  const oldId = oldSnapshot.state.facts.temperature_c.fact_id;
  await series().getByRole("button", { name: "再测一次", exact: true }).click();
  await expect(
    page.locator("#active-task .observation-previous"),
  ).toContainText("38.25 ℃");
  await expect(page.locator("#active-task .step-progress")).toContainText(
    "1 / 3",
  );
  await send("腋温37.6℃");
  await expect(series().locator(".observation-value")).toHaveText("37.6 ℃");
  await expect(
    current().locator(".summary-item[data-fact-key='temperature_c']"),
  ).toHaveCount(0);
  await series().locator(".observation-details > summary").click();
  await expect(series().locator(".observation-history li")).toHaveCount(2);
  await expect(series().locator(".observation-history")).toContainText(
    "38.25 ℃",
  );
  await expect(series()).toContainText("系统记录时间");
  if (screenshotPrefix)
    await page.screenshot({
      path: `${screenshotPrefix}-history.png`,
      fullPage: true,
    });
  await series().getByRole("button", { name: "再测一次", exact: true }).click();
  await expect(page.locator("#active-task .step-progress")).toContainText(
    "1 / 3",
  );
  await send("38.6还是39.2");
  await expect(page.locator("#active-task .task-feedback")).toBeVisible();
  await page.getByRole("button", { name: "暂时跳过", exact: true }).click();
  await expect(series().locator(".observation-value")).toHaveText("37.6 ℃");
  await expect(series()).toContainText("上次记录 · 本次未完成");
  await series().locator(".observation-details > summary").click();
  await expect(series().locator(".observation-history")).toContainText(
    "本次跳过",
  );
  if (screenshotPrefix)
    await page.screenshot({
      path: `${screenshotPrefix}-skipped.png`,
      fullPage: true,
    });
  await send("我刚刚又测了一次腋温40.1℃，想重新测量。");
  await expect(
    page.locator("#active-task .observation-proposal .observation-value"),
  ).toHaveText("40.1 ℃");
  await expect(
    page.locator("#active-task .observation-previous"),
  ).toContainText("37.6 ℃");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  if (screenshotPrefix)
    await page.screenshot({
      path: `${screenshotPrefix}-candidate.png`,
      fullPage: true,
    });
  await page
    .getByRole("button", { name: "核对并记录 40.1℃", exact: true })
    .click();
  await expect(series().locator(".observation-value")).toHaveText("40.1 ℃");
  await expect(current().locator(".observation-repeat")).toHaveCount(0);
  // The 40.1 ℃ reading closes the session, so the conclusion sheet takes
  // over the routing guidance and the record card deliberately stops
  // rendering `.summary-route` (结论单 §7 de-duplication). Assert the
  // escalation where it is actually shown, and assert the de-duplication
  // itself: two "what to do next" blocks on one screen is the failure
  // mode this guards.
  await expect(current().locator(".summary-route")).toHaveCount(0);
  const disposition = page.locator(".conclusion-card .conclusion-disposition");
  await expect(disposition).toHaveClass(/urgent_today/);
  await expect(disposition).toContainText("今天");
  // The record still has to read as alarming, so the value keeps the
  // urgent accent. Compared against the token rather than a sibling
  // element: the route section that used to carry the colour is gone here.
  const urgentColour = await current().evaluate((node) => {
    const probe = document.createElement("span");
    probe.style.color = "var(--orange)";
    node.append(probe);
    const colour = getComputedStyle(probe).color;
    probe.remove();
    return colour;
  });
  await expect(series().locator(".observation-value")).toHaveCSS(
    "color",
    urgentColour,
  );
  if (screenshotPrefix)
    await page.screenshot({
      path: `${screenshotPrefix}-urgent.png`,
      fullPage: true,
    });
  const snapshot = await page.evaluate(
    async (sessionId) =>
      (await fetch(`/api/sessions/${sessionId}/snapshot`)).json(),
    id,
  );
  expect(snapshot.state.facts.temperature_c.value).toBe(40.1);
  const readings = snapshot.state.history.filter(
    (fact) => fact.key === "temperature_c",
  );
  expect(readings.map((fact) => fact.value)).toEqual([38.25, 37.6, 40.1]);
  expect(readings[1].supersedes).toBe(oldId);
  expect(
    snapshot.state.observation_attempts.map((item) => item.status),
  ).toEqual(["recorded", "skipped", "recorded"]);
  // Four scripted turns plus the ordering retry inside the temperature
  // turn — the same extra call accounted for above. Reading a temperature
  // off the task goes through the deterministic parser, not the model, so
  // the three repeat attempts add nothing here.
  expect(model.requests).toHaveLength(5);
  expect(
    snapshot.state.observation_attempts.every((item) => item.finished_at),
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
}

export async function verifyResumedObservationQuestion(
  page,
  model,
  screenshot,
) {
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const send = (text) => sendPatientText(page, text);
  await send(opening);
  await expect(page.locator("#active-task")).toBeVisible();
  await send("腋温38.25℃");
  await expect(page.locator("#active-question")).toBeVisible();
  const question = await page.locator("#active-question").textContent();
  await send("想重新测温");
  await expect(
    page.locator("#active-task .observation-previous"),
  ).toContainText("38.25 ℃");
  await send("腋温37.6℃");
  await expect(page.locator("#active-question")).toHaveText(question);
  await expect(
    page.locator("#active-observation .observation-value"),
  ).toHaveText("37.6 ℃");
  await page
    .locator("#active-observation")
    .getByRole("button", { name: "再测一次", exact: true })
    .click();
  await page.getByRole("button", { name: "暂时跳过", exact: true }).click();
  await expect(page.locator("#active-question")).toHaveText(question);
  await expect(page.locator("#active-observation")).toContainText(
    "上次记录 · 本次未完成",
  );
  // Opening, the temperature turn, and one ordering retry inside it: at
  // 38.25℃ the respiratory pathway outranks `trajectory`, but the context
  // lists candidates in content order, so the script cannot know that
  // until the engine says so.
  expect(model.requests).toHaveLength(3);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(
    page.getByRole("textbox", { name: "输入你的描述" }),
  ).toBeInViewport();
  // Which question the engine asks is its call — for a febrile patient it
  // asks the respiratory detail first, and that one is free text with no
  // option buttons. Pinning "four choices" was really pinning the old
  // question. The layout claim holds either way: the card is fully on
  // screen, and so is every choice when there are choices.
  const card = page.locator("#active-question");
  if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });
  await expect(card).toBeVisible();
  await expect(card).toBeInViewport({ ratio: 1 });
  const choices = card.getByRole("button");
  for (const choice of await choices.all())
    await expect(choice).toBeInViewport({ ratio: 1 });
  if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });
}
