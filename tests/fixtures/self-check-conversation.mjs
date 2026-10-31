import { createServer } from "node:http";
import { nextCommand } from "./model-feedback.mjs";
import { once } from "node:events";
import { expect } from "@playwright/test";

export async function createSelfCheckConversationModel(option = "head") {
  const record = (facts) => ({ tool: "record_facts", arguments: { facts } });
  const fact = (key, value, evidence, assertion = "present") => ({
    key,
    value,
    evidence,
    assertion,
  });
  const ask = { tool: "ask_question", arguments: { question_id: "onset" } };
  const commands = [
    record([
      fact("consult_for", "self", "我自己"),
      fact("age_years", 32, "32岁"),
      fact("chief_complaint", "头疼", "头疼"),
      fact("emergency_screen", null, "上述危险信号都没有", "absent"),
    ]),
    { tool: "show_self_check", arguments: { task_id: "body_location" } },
    record([fact("medications_taken", "昨晚用过药物甲", "昨晚用过药物甲")]),
    ask,
    {
      tool: "propose_self_check_result",
      arguments: {
        task_id: "body_location",
        option,
        evidence: option === "other" ? "是在其他部位" : "右太阳穴附近疼",
      },
    },
    ask,
  ];
  const requests = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    const command = nextCommand(body, commands);
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
          : { error: "Fixture exhausted" },
      ),
    );
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return {
    endpoint: `http://127.0.0.1:${server.address().port}/v1`,
    requests,
    option,
    async close() {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

export async function verifySelfCheckConversation(
  page,
  model,
  { shot, manual = false } = {},
) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  const snapshot = () =>
    page.evaluate(
      async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
      id,
    );
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  const send = async (text) => {
    await input.fill(text);
    await page.getByRole("button", { name: "发送回答" }).click();
    await expect(page.locator("#send-button")).toBeEnabled();
  };
  await send("我自己32岁，头疼；上述危险信号都没有。");
  await expect(page.locator("#active-task .body-map")).toBeVisible();
  const offered = await snapshot();
  const task = offered.events
    .filter((e) => e.event_type === "turn.agent_reply")
    .at(-1).payload.reply.task;
  await page
    .locator(".body-map")
    .getByRole("button", { name: "四肢", exact: true })
    .click();
  await page.locator(".location-step-guide > summary").click();
  await page
    .locator("#active-task")
    .getByRole("button", { name: "下一步", exact: true })
    .click();
  await send("昨晚用过药物甲。");
  await expect(page.locator("#active-question h2")).toBeVisible();
  const paused = await snapshot();
  expect(paused.state.facts.symptom_location).toBeUndefined();
  expect(paused.state.facts.medications_taken.value).toBe("昨晚用过药物甲");
  expect(paused.state.suspended_self_check.interaction_id).toBe(
    task.interaction_id,
  );
  const calls = model.requests.length;
  await page.getByRole("button", { name: /^继续指出最明显/ }).click();
  await expect(
    page.getByRole("button", { name: "记录：四肢", exact: true }),
  ).toBeEnabled();
  await expect(page.locator("#active-task .step-progress > span")).toHaveText(
    "2 / 2",
  );
  expect(model.requests).toHaveLength(calls);
  const raw = model.option === "other" ? "是在其他部位" : "右太阳穴附近疼";
  await send(raw);
  const proposal = page.locator("#active-task .body-proposal");
  await expect(proposal).toHaveText(`请核对部位 · “${raw}”`);
  const before = await snapshot();
  expect(before.state.facts.symptom_location).toBeUndefined();
  const evidenceTurn = before.events
    .filter((e) => e.event_type === "turn.user_message")
    .at(-1).turn_id;
  const label = model.option === "other" ? "其他部位" : "头/面部";
  let confirm = page.getByRole("button", {
    name: `核对并记录：${label}`,
    exact: true,
  });
  await expect(confirm).toBeEnabled();
  // Set LAYOUT_DIAG=1 to dump the box chain when the visibility check
  // below fails. The assertion says "off screen"; it does not say which
  // box grew or where the space went.
  if (process.env.LAYOUT_DIAG) {
    const chain = await page.evaluate(() => {
      const box = (selector) => {
        const node = document.querySelector(selector);
        if (!node) return null;
        const r = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return {
          selector,
          top: Math.round(r.top * 100) / 100,
          bottom: Math.round(r.bottom * 100) / 100,
          height: Math.round(r.height * 100) / 100,
          minHeight: style.minHeight,
          overflowY: style.overflowY,
          padding: style.padding,
        };
      };
      return {
        innerHeight,
        scrollY,
        docScroll: document.documentElement.scrollHeight,
        body: box("body"),
        main: box("main"),
        view: box("#view-consultation"),
        grid: box(".consult-grid"),
        panel: box(".conversation-panel"),
        activeTask: box("#active-task"),
        scroll: box("#conversation-scroll"),
        footer: box(".conversation-footer"),
        input: box("#message-input"),
        urgent: box("#urgent-help"),
      };
    });
    console.error("LAYOUT", JSON.stringify(chain, null, 1));
  }
  const geometries = await page.evaluate(() => {
    const pane = document.querySelector("#active-task").getBoundingClientRect();
    return [
      "#active-task h3",
      ".body-proposal",
      ".body-map-canvas",
      ".map-confirm",
      "#message-input",
      "#urgent-help",
    ].map((selector) => {
      const element = document.querySelector(selector);
      const rect = element.getBoundingClientRect();
      return {
        selector,
        top: rect.top,
        bottom: rect.bottom,
        visible:
          rect.top >= 0 &&
          rect.bottom <= innerHeight &&
          (selector === "#message-input" ||
            selector === "#urgent-help" ||
            (rect.top >= pane.top && rect.bottom <= pane.bottom)),
      };
    });
  });
  expect(geometries.filter((item) => !item.visible)).toEqual([]);
  if (manual) {
    await page.getByRole("button", { name: "修改部位", exact: true }).click();
    await page
      .locator(".body-map")
      .getByRole("button", { name: "胸部", exact: true })
      .click();
    confirm = page.getByRole("button", { name: "记录：胸部", exact: true });
  }
  const draft = "这段过敏补充尚未发送。";
  await input.fill(draft);
  await confirm.scrollIntoViewIfNeeded();
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  await confirm.evaluate((node) => {
    window.oldConversationConfirm = node;
  });
  await confirm.click();
  await expect(page.locator("#send-button")).toBeEnabled();
  const after = await snapshot();
  const result = after.state.facts.symptom_location;
  expect(result.value).toBe(manual ? "胸部" : label);
  expect(result.evidence).toBe(manual ? "胸部" : raw);
  if (!manual) expect(result.turn_id).toBe(evidenceTurn);
  expect(result.quality).toBe("not_assessed");
  expect(result.source).toBe("patient");
  await expect(input).toHaveValue(draft);
  await page.evaluate(() => {
    window.oldConversationConfirm.disabled = false;
    window.oldConversationConfirm.click();
  });
  expect((await snapshot()).events).toEqual(after.events);
  // Six scripted turns plus two compliance retries: the engine rejects a
  // proposal that breaks question ordering or leaves a triage field
  // unattempted, and nextCommand answers it without advancing the script.
  // The count still catches a runaway loop.
  expect(model.requests).toHaveLength(8);
  expect(errors).toEqual([]);
}
