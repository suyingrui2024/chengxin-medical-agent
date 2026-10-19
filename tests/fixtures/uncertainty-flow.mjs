import { expect } from "@playwright/test";

export const medicationReport = "昨晚用过药物甲，用量不清楚";
export const allergyReport = "以前有过皮疹，是否药物过敏不确定";

async function snapshot(page, id) {
  return page.evaluate(async (sessionId) => {
    const response = await fetch(`/api/sessions/${sessionId}/snapshot`);
    if (!response.ok) throw new Error(`Snapshot failed: ${response.status}`);
    return response.json();
  }, id);
}

export async function exerciseUncertainReports(page, screenshot) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  const created = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  const answers = {
    consult_for: "我自己",
    age: "32岁",
    chief_complaint: "手臂疼痛",
    emergency_screen: "都没有",
    onset: "昨天",
    trajectory: "变化不大",
    severity: "3分",
    associated_symptoms: "没有",
    medications_taken: medicationReport,
    allergies: allergyReport,
    relevant_history: "没有",
    patient_goal: "整理就医记录",
  };
  let data;
  for (let turn = 0; turn < 30; turn++) {
    data = await snapshot(page, id);
    if (["summary", "closed"].includes(data.state.phase)) break;
    if (data.state.active_task) {
      const reply = data.events
        .filter((event) => event.event_type === "turn.agent_reply")
        .at(-1).payload.reply;
      const skip = reply.quick_replies.find(
        (choice) => choice.value === "skip",
      );
      if (!skip) throw new Error("Active task has no registered skip option");
      await page.getByRole("button", { name: skip.label, exact: true }).click();
    } else {
      const text = answers[data.state.active_question];
      if (!text)
        throw new Error(`Unexpected question: ${data.state.active_question}`);
      await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
      await page.getByRole("button", { name: "发送回答" }).click();
    }
    await expect(page.locator("#busy-indicator")).toBeHidden();
  }
  expect(data.state.phase).toBe("summary");
  const current = () => page.locator(".summary-card.current-summary");
  const report = (key) =>
    current().locator(`.unknown-report[data-fact-key='${key}']`);
  await expect(report("medications_taken").locator("blockquote")).toHaveText(
    medicationReport,
  );
  await expect(report("allergies").locator("blockquote")).toHaveText(
    allergyReport,
  );
  await expect(report("medications_taken")).toContainText("尚未确认");
  const controls = await current()
    .locator(".unknown-report button")
    .evaluateAll((buttons) =>
      buttons.map((button) => button.getBoundingClientRect().height),
    );
  expect(controls.every((height) => height >= 44)).toBe(true);
  expect(data.state.facts.medications_taken.value).toBeNull();
  expect(data.state.facts.allergies.assertion).toBe("unknown");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  if (screenshot) {
    if (page.viewportSize()?.width < 600)
      await report("medications_taken").scrollIntoViewIfNeeded();
    await page.screenshot({ path: screenshot, fullPage: true });
  }

  // Start from the actual retained quote, keep the unresolved part, and confirm
  // that a correction replaces current evidence rather than reviving old text.
  await report("medications_taken")
    .getByRole("button", { name: "补充 / 更正", exact: true })
    .click();
  const editor = page.getByLabel("更正「用药与处理」");
  await expect(editor).toHaveValue(medicationReport);
  const updated = "昨晚用过药物甲，具体用量仍不清楚，准备带包装就医";
  await editor.fill(updated);
  await page.getByRole("button", { name: "保存并重新核对" }).click();
  await expect(page.locator("#edit-dialog")).toBeHidden();
  await expect(report("medications_taken").locator("blockquote")).toHaveText(
    updated,
  );
  data = await snapshot(page, id);
  const history = data.state.history.filter(
    (fact) => fact.key === "medications_taken",
  );
  expect(history.map((fact) => fact.evidence)).toEqual([
    medicationReport,
    updated,
  ]);
  expect(history[1].supersedes).toBe(history[0].fact_id);
  expect(history[1].assertion).toBe("unknown");
  expect(
    data.events.filter((event) => event.event_type === "model.requested"),
  ).toHaveLength(0);
  await page
    .getByRole("button", { name: "确认摘要并结束本次问诊", exact: true })
    .click();
  await expect(page.locator("#composer")).toBeHidden();
  await expect(page.locator(".summary-edit:enabled")).toHaveCount(0);
  await expect(page.locator(".summary-card").last()).toContainText(updated);
  expect(errors).toEqual([]);
}
