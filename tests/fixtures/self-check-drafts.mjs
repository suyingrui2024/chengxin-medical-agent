import { expect } from "@playwright/test";
import { verifyLocationLayout } from "./location-layout.mjs";
import { reachDegree, degreeSnapshot } from "./visual-answer-drafts.mjs";

export async function reachSelfCheck(page, complaint = "头疼") {
  const id = await reachDegree(page, { complaint });
  await page.getByRole("button", { name: "3 分", exact: true }).click();
  await page.getByRole("button", { name: "确认 3 分", exact: true }).click();
  await expect(page.locator("#send-button")).toBeEnabled();
  // History-phase questions are answered inside reachDegree now; what
  // remains before the task offer depends on the planner's ordering, so
  // keep answering by question id until a task is offered.
  const answers = {
    onset: "昨天逐渐出现",
    resp_breathing: "能正常呼吸",
    resp_cough_details: "干咳",
    resp_intake: "能正常喝水吃饭",
    gi_details: "肚脐周围隐痛",
    head_details: "后脑勺胀痛",
    dizzy_details: "没有",
    back_details: "没有",
    urinary_details: "没有",
    skin_details: "没有",
    associated_symptoms: "没有",
    medications_taken: "没有",
    allergies: "没有",
    relevant_history: "没有",
    patient_goal: "整理信息",
    // 发热路径下 ranker 会在 severity 之后追问自述体温；答“没量过”不触发
    // 体温阈值分支，保持 facts 最小（与本 map 其余“没有”系答案同款）。
    reported_temperature: "没量过",
  };
  for (let step = 0; step < 12; step++) {
    const snapshot = await degreeSnapshot(page, id);
    if (snapshot.state.active_task) {
      await expect(page.locator("#active-task .task-card")).toBeVisible();
      return id;
    }
    const question = snapshot.events
      .filter((e) => e.event_type === "turn.agent_reply")
      .at(-1).payload.reply.question_id;
    if (question === "trajectory") {
      // Severity can now precede trajectory under the differential
      // planner; trajectory is a choice question, answered by button.
      await page.getByRole("button", { name: "变化不大", exact: true }).click();
      await expect(page.locator("#send-button")).toBeEnabled();
      continue;
    }
    expect(
      answers[question],
      `Expected a known synthetic answer for ${question}`,
    ).toBeDefined();
    await page
      .getByRole("textbox", { name: "输入你的描述" })
      .fill(answers[question]);
    await page.getByRole("button", { name: "发送回答" }).click();
    await expect(page.locator("#send-button")).toBeEnabled();
  }
  throw new Error("Expected a registered self-check");
}

export async function verifyLocationDraft(page, { shot } = {}) {
  const id = await reachSelfCheck(page);
  await verifyLocationLayout(page, id);
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  const draft = "右臂外侧，稍后再补充。";
  await page
    .locator(".body-map")
    .getByRole("button", { name: "四肢", exact: true })
    .click();
  await page.locator("#active-task .location-step-guide > summary").click();
  await page
    .locator("#active-task")
    .getByRole("button", { name: "下一步", exact: true })
    .click();
  await expect(page.locator("#active-task .step-progress > span")).toHaveText(
    "2 / 2",
  );
  await page.getByRole("button", { name: "展开对话记录", exact: true }).click();
  await page.getByRole("button", { name: "返回当前任务", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "记录：四肢", exact: true }),
  ).toBeEnabled();
  await input.fill(draft);
  const before = await degreeSnapshot(page, id);
  expect(before.state.facts.symptom_location).toBeUndefined();
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  if (await page.locator("#mobile-menu").isVisible())
    await page.locator("#mobile-menu").click();
  await page.locator("#new-session").click();
  const otherId = (await (await created).json()).session_id;
  expect((await degreeSnapshot(page, otherId)).state.facts).toEqual({});
  if (await page.locator("#mobile-menu").isVisible())
    await page.locator("#mobile-menu").click();
  await page.locator(`#recent-sessions [data-session-id="${id}"]`).click();
  await expect(input).toHaveValue(draft);
  const confirm = page.getByRole("button", { name: "记录：四肢", exact: true });
  await expect(confirm).toBeEnabled();
  await expect(page.locator("#active-task .step-progress > span")).toHaveText(
    "2 / 2",
  );
  const restored = await degreeSnapshot(page, id);
  expect(restored.events).toEqual(before.events);
  expect(restored.state.facts).toEqual(before.state.facts);
  await confirm.scrollIntoViewIfNeeded();
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  await confirm.evaluate((node) => {
    window.oldLocationConfirm = node;
  });
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.url().endsWith(`/sessions/${id}/messages`),
  );
  await confirm.click();
  const result = await (await response).json();
  expect(result.state.facts.symptom_location.value).toBe("四肢");
  expect(result.state.facts.symptom_location.quality).toBe("not_assessed");
  await expect(input).toHaveValue(draft);
  const completed = await degreeSnapshot(page, id);
  expect(
    completed.events
      .slice(before.events.length)
      .filter((e) => e.event_type === "turn.user_message"),
  ).toHaveLength(1);
  await page.evaluate(() => {
    window.oldLocationConfirm.disabled = false;
    window.oldLocationConfirm.click();
  });
  expect((await degreeSnapshot(page, id)).events).toEqual(completed.events);
}

export async function verifyTemperatureProgress(page, { shot } = {}) {
  const id = await reachSelfCheck(page, "发热");
  await page
    .locator("#active-task")
    .getByRole("button", { name: "下一步", exact: true })
    .click();
  await page
    .locator("#active-task")
    .getByRole("button", { name: "下一步", exact: true })
    .click();
  await expect(page.locator("#active-task .step-progress > span")).toHaveText(
    "3 / 3",
  );
  const before = await degreeSnapshot(page, id);
  const offered = before.events
    .filter((e) => e.event_type === "turn.agent_reply")
    .at(-1).payload.reply;
  await page.getByRole("textbox", { name: "输入你的描述" }).fill("abc");
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(page.locator("#active-task .task-feedback")).toBeVisible();
  await expect(page.locator("#send-button")).toBeEnabled();
  await expect(page.locator("#active-task .step-progress > span")).toHaveText(
    "3 / 3",
  );
  const invalid = await degreeSnapshot(page, id);
  const reply = invalid.events
    .filter((e) => e.event_type === "turn.agent_reply")
    .at(-1).payload.reply;
  expect(reply.action_id).not.toBe(offered.action_id);
  expect(reply.task.interaction_id).toBe(offered.task.interaction_id);
  expect(invalid.state.facts).toEqual(before.state.facts);
  const feedbackVisible = await page.evaluate(() => {
    const pane = document.querySelector("#active-task").getBoundingClientRect();
    const message = document
      .querySelector("#active-task .task-feedback")
      .getBoundingClientRect();
    return (
      message.top >= pane.top &&
      message.bottom <= pane.bottom &&
      message.bottom <= innerHeight
    );
  });
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  expect(feedbackVisible).toBe(true);
}
