import { createServer } from "node:http";
import { once } from "node:events";
import { expect } from "@playwright/test";

const firstText = "我父亲68岁，头疼。";
const nextText = "改问我母亲，她65岁，肚子痛。";
const record = (person, age, complaint) => ({
  tool: "record_facts",
  arguments: {
    facts: [
      {
        key: "consult_for",
        value: "other_adult",
        assertion: "present",
        evidence: person,
      },
      {
        key: "age_years",
        value: age,
        assertion: "present",
        evidence: `${age}岁`,
      },
      {
        key: "chief_complaint",
        value: complaint,
        assertion: "present",
        evidence: complaint,
      },
    ],
  },
});
const ask = {
  tool: "ask_question",
  arguments: { question_id: "emergency_screen" },
};

export async function createConsultationSubjectsModel() {
  const requests = [],
    commands = [
      record("我父亲", 68, "头疼"),
      ask,
      record("我母亲", 65, "肚子痛"),
      ask,
    ];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    requests.push(JSON.parse(raw));
    const command = commands.shift();
    response.writeHead(command ? 200 : 500, {
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

export async function verifyConsultationSubjects(
  page,
  model,
  { shots, mobile = false } = {},
) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const send = async (text) => {
    const pending = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        /\/api\/sessions\/[^/]+\/messages$/.test(r.url()),
    );
    await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
    await page.getByRole("button", { name: "发送回答" }).click();
    return (await pending).json();
  };
  const snapshot = (id) =>
    page.evaluate(
      async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
      id,
    );
  const first = await send(firstText),
    fatherId = first.state.session_id;
  const facts = first.state.facts;
  await expect(page.locator("#conversation-subtitle")).toHaveText(
    "父亲 · 家属代问 · 直接模型",
  );
  const question = await page.locator("#active-question h2").textContent();
  const changed = await send(nextText);
  const card = page.locator("#active-question .subject-change-card");
  await expect(card).toBeVisible();
  await expect(card.locator(".subject-current strong")).toHaveText("父亲");
  await expect(card.locator(".subject-requested strong")).toHaveText("母亲");
  expect(changed.state.facts).toEqual(facts);
  expect(model.requests).toHaveLength(2);
  if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  const newCase = card.getByRole("button", {
    name: "为母亲新建问诊",
    exact: true,
  });
  const resume = card.getByRole("button", {
    name: "继续父亲问诊",
    exact: true,
  });
  const area = await page.locator("#active-question").boundingBox();
  for (const button of [newCase, resume]) {
    const box = await button.boundingBox();
    expect(box.y).toBeGreaterThanOrEqual(area.y);
    expect(box.y + box.height).toBeLessThanOrEqual(area.y + area.height);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  if (shots) await page.screenshot({ path: `${shots}-choice.png` });
  await resume.click();
  await expect(page.locator("#active-question h2")).toHaveText(question);
  expect(model.requests).toHaveLength(2);
  await send(nextText);
  const stale = await page.evaluate(
    async ({ id, action }) =>
      (
        await fetch(`/api/sessions/${id}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: "旧入口",
            choice_value: "resume_current_subject",
            expected_action_id: action,
          }),
        })
      ).status,
    { id: fatherId, action: changed.reply.action_id },
  );
  expect(stale).toBe(409);
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await newCase.click();
  const mother = await (await created).json(),
    motherId = mother.state.session_id;
  expect(motherId).not.toBe(fatherId);
  expect(mother.state.facts).toEqual({});
  await expect(page.locator("#active-question h2")).toHaveText(
    "核对母亲的原话",
  );
  await expect(page.locator("#active-question .visual-choice")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "输入你的描述" })).toHaveValue(
    nextText,
  );
  expect(model.requests).toHaveLength(2);
  if (shots) await page.screenshot({ path: `${shots}-draft.png` });
  if (mobile) await page.setViewportSize({ width: 1440, height: 1080 });
  await page
    .locator(`#recent-sessions .recent-item[data-session-id="${fatherId}"]`)
    .click();
  await expect(page.getByRole("textbox", { name: "输入你的描述" })).toHaveValue(
    "",
  );
  await page
    .locator(`#recent-sessions .recent-item[data-session-id="${motherId}"]`)
    .click();
  await expect(page.getByRole("textbox", { name: "输入你的描述" })).toHaveValue(
    nextText,
  );
  await expect(page.locator("#active-question h2")).toHaveText(
    "核对母亲的原话",
  );
  expect(model.requests).toHaveLength(2);
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(page.locator("#conversation-subtitle")).toHaveText(
    "母亲 · 家属代问 · 直接模型",
  );
  expect(model.requests).toHaveLength(4);
  const father = await snapshot(fatherId),
    motherState = await snapshot(motherId);
  expect(father.state.facts).toEqual(facts);
  expect(motherState.state.facts.age_years.value).toBe(65);
  expect(motherState.state.consultation_subject.label).toBe("母亲");
  if (mobile) await page.setViewportSize({ width: 1440, height: 1080 });
  const settings = await page.evaluate(async () => {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ backend: "demo" }),
    });
    return response.ok;
  });
  expect(settings).toBe(true);
  await page.reload();
  await page
    .locator(`#recent-sessions .recent-item[data-session-id="${fatherId}"]`)
    .click();
  await expect(card).toBeVisible();
  await expect(page.locator("#conversation-subtitle")).toHaveText(
    "父亲 · 家属代问 · 直接模型",
  );
  await resume.click();
  await expect(page.locator("#active-question h2")).toHaveText(question);
  expect(model.requests).toHaveLength(4);
  expect(errors).toEqual([]);
}
