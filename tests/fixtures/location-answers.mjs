import { createServer } from "node:http";
import { nextCommand } from "./model-feedback.mjs";
import { once } from "node:events";
import { expect } from "@playwright/test";
import { degreeSnapshot } from "./visual-answer-drafts.mjs";

export const detailQuestion =
  "你说的头痛具体在头部哪个区域或哪一侧？包括范围是否清楚？";
export async function createLocationAnswersModel(
  prefill = false,
  { nextQuestion = "onset" } = {},
) {
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
        message: detailQuestion,
        answer_fields: ["region", "area", "side", "extent"],
      },
    },
    ...(prefill
      ? [
          {
            tool: "propose_self_check_result",
            arguments: {
              task_id: "body_location",
              option: "head",
              evidence: "右太阳穴附近，是一片范围",
              input: {
                side: "right",
                area: { kind: "preset", value: "temple" },
                extent: "area",
              },
            },
          },
        ]
      : []),
    { tool: "ask_question", arguments: { question_id: nextQuestion } },
  ];
  const requests = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const part of request) raw += part;
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
    prefill,
    async close() {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

export async function verifyLocationAnswers(
  page,
  model,
  { shotPrefix, custom = false } = {},
) {
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
  await send("可能在右太阳穴附近，位置还没说清楚。");
  const pane = page.locator("#active-task");
  await expect(pane.locator("h3")).toHaveText(detailQuestion);
  await expect(pane.locator(".location-answer")).toBeVisible();
  const before = await degreeSnapshot(page, id);
  expect(before.state.facts.symptom_location).toBeUndefined();
  const calls = model.requests.length;
  const next = async () => {
    await pane.getByRole("button", { name: "下一步", exact: true }).click();
  };
  const geometry = async (stage) => {
    await pane.locator(".location-answer .primary-button").first().hover();
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
    const measurements = await page.evaluate(() => {
      const pane = document
        .querySelector("#active-task")
        .getBoundingClientRect();
      const selectors = [
        "#active-task h3",
        ".location-answer-progress",
        ".location-answer-field",
        ".location-answer-review",
        ".location-answer-navigation",
        ".location-answer-final-actions",
        "#message-input",
        "#urgent-help",
      ];
      return selectors.flatMap((selector) => {
        const element = document.querySelector(selector);
        if (!element) return [];
        const rect = element.getBoundingClientRect();
        return [
          {
            selector,
            top: rect.top,
            bottom: rect.bottom,
            paneBottom: pane.bottom,
            visible:
              rect.top >= -0.5 &&
              rect.bottom <= innerHeight + 0.5 &&
              rect.left >= -0.5 &&
              rect.right <= innerWidth + 0.5 &&
              (["#message-input", "#urgent-help"].includes(selector) ||
                (rect.top >= pane.top - 0.5 &&
                  rect.bottom <= pane.bottom + 0.5)),
          },
        ];
      });
    });
    expect(
      measurements.filter((x) => !x.visible),
      `${stage} must fit its actual task pane`,
    ).toEqual([]);
    const smallTargets = await pane
      .locator(".location-answer-control")
      .evaluateAll((nodes) =>
        nodes.flatMap((node) => {
          const rect = node.getBoundingClientRect();
          return rect.width < 44 || rect.height < 44
            ? [
                {
                  label: node.textContent,
                  width: rect.width,
                  height: rect.height,
                },
              ]
            : [];
        }),
      );
    expect(smallTargets, `${stage} controls must remain at least 44px`).toEqual(
      [],
    );
    const lowContrast = await pane
      .locator(".location-answer-control:enabled")
      .evaluateAll((nodes) => {
        const luminance = (color) => {
          const channels = color
            .match(/[\d.]+/g)
            .slice(0, 3)
            .map(Number)
            .map((x) => x / 255)
            .map((x) =>
              x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4,
            );
          return (
            channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
          );
        };
        return nodes.flatMap((node) => {
          const style = getComputedStyle(node);
          const a = luminance(style.color),
            b = luminance(style.backgroundColor);
          const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
          return ratio < 4.5
            ? [
                {
                  label: node.textContent,
                  color: style.color,
                  background: style.backgroundColor,
                  ratio,
                },
              ]
            : [];
        });
      });
    expect(
      lowContrast,
      `${stage} normal and hovered controls need readable text`,
    ).toEqual([]);
    await page.evaluate(
      ({ stage, measurements }) => {
        window.locationAnswerGeometry ||= [];
        window.locationAnswerGeometry.push({ stage, measurements });
      },
      { stage, measurements },
    );
  };
  if (model.prefill) {
    await send("右太阳穴附近，是一片范围");
    await expect(
      pane.getByRole("button", { name: "左右 · 右侧", exact: true }),
    ).toBeVisible();
    await pane
      .getByRole("button", { name: "左右 · 右侧", exact: true })
      .click();
    await pane.getByRole("button", { name: "左侧", exact: true }).click();
    await next();
    await expect(
      pane.getByRole("button", { name: "一片范围", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await next();
  } else {
    await pane.getByRole("button", { name: "头/面部", exact: true }).click();
    await next();
    await expect(pane.locator(".location-answer-progress > span")).toHaveText(
      "2 / 4 · 具体位置",
    );
    if (custom) {
      await pane.getByRole("button", { name: "自己描述", exact: true }).click();
      await pane
        .getByRole("textbox", { name: "描述具体位置" })
        .fill("耳朵后面靠近头发的位置");
    } else
      await pane
        .getByRole("button", { name: "太阳穴附近", exact: true })
        .click();
    await geometry("area");
    if (shotPrefix)
      await page.screenshot({ path: `${shotPrefix}-area.png`, fullPage: true });
    await next();
    await pane
      .getByRole("button", { name: custom ? "说不清" : "右侧", exact: true })
      .click();
    await next();
    await pane
      .getByRole("button", {
        name: custom ? "说不清" : "一片范围",
        exact: true,
      })
      .click();
    await geometry("extent");
    if (shotPrefix)
      await page.screenshot({
        path: `${shotPrefix}-extent.png`,
        fullPage: true,
      });
    await next();
    expect(model.requests).toHaveLength(calls);
  }
  await expect(
    pane.getByRole("button", {
      name: `左右 · ${custom ? "说不清" : model.prefill ? "左侧" : "右侧"}`,
      exact: true,
    }),
  ).toBeVisible();
  expect(
    (await degreeSnapshot(page, id)).state.facts.symptom_location,
  ).toBeUndefined();
  const draft = "这段用药补充还没有发送。";
  await input.fill(draft);
  if (model.prefill) {
    const snapshot = await degreeSnapshot(page, id);
    await page.locator('.nav-item[data-view="history"]').click();
    await page.locator(`#recent-sessions [data-session-id="${id}"]`).click();
    await expect(input).toHaveValue(draft);
    await expect(
      pane.getByRole("button", { name: "左右 · 左侧", exact: true }),
    ).toBeVisible();
    expect((await degreeSnapshot(page, id)).events).toEqual(snapshot.events);
    expect(model.requests).toHaveLength(calls + 1);
  }
  await geometry("review");
  if (shotPrefix)
    await page.screenshot({ path: `${shotPrefix}-review.png`, fullPage: true });
  const confirm = pane.getByRole("button", {
    name: "核对并记录位置",
    exact: true,
  });
  await confirm.evaluate((node) => {
    window.oldDetailConfirm = node;
  });
  await confirm.click();
  await expect(page.locator("#send-button")).toBeEnabled();
  const after = await degreeSnapshot(page, id);
  const result = after.state.facts.symptom_location;
  expect(result.structured_input.answers.side.value).toBe(
    custom ? null : model.prefill ? "left" : "right",
  );
  expect(result.structured_input.answers.area.value).toBe(
    custom ? "耳朵后面靠近头发的位置" : "temple",
  );
  expect(result.structured_input.answers.extent.value).toBe(
    custom ? null : "area",
  );
  if (custom) {
    expect(result.structured_input.answers.extent.assertion).toBe("unknown");
    expect(result.structured_input.answers.side.assertion).toBe("unknown");
  }
  expect(result.quality).toBe("not_assessed");
  expect(result.source).toBe("patient");
  expect(
    after.events.filter(
      (e) =>
        e.event_type === "fact.recorded" &&
        e.payload.key === "symptom_location",
    ),
  ).toHaveLength(1);
  if (model.prefill)
    expect(result.structured_input.proposal_source.answers.side).toBe("right");
  await expect(input).toHaveValue(draft);
  await page.evaluate(() => {
    window.oldDetailConfirm.disabled = false;
    window.oldDetailConfirm.click();
  });
  expect((await degreeSnapshot(page, id)).events).toEqual(after.events);
  // One more call than scripted turns: the engine rejects a proposal that
  // breaks question ordering or leaves a triage field unattempted, and
  // nextCommand answers it without advancing the script. Still exact, so a
  // runaway loop is caught.
  expect(model.requests).toHaveLength(model.prefill ? 6 : 5);
  expect(errors).toEqual([]);
  return { sessionId: id, result, snapshot: after };
}
