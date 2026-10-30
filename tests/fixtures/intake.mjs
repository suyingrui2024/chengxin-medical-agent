import { expect } from "@playwright/test";

/**
 * Dynamic intake helpers.
 *
 * The differential planner orders history questions by discrimination value,
 * not bank order, and the opening order is consult_for -> sex -> age. Tests
 * that hard-code a question sequence desync as soon as the planner or content
 * changes, so these helpers read the *actual* pending question id from the
 * session snapshot and answer it from a synthetic map.
 */

export const INTAKE_ANSWERS = {
  onset: "昨天逐渐出现",
  associated_symptoms: "没有其他不适",
  head_details: "后脑勺胀痛",
  resp_breathing: "呼吸正常",
  resp_cough_details: "没有咳嗽",
  resp_intake: "能正常喝水吃饭",
  gi_details: "肚脐周围隐痛",
  dizzy_details: "没有",
  back_details: "没有",
  urinary_details: "没有",
  skin_details: "没有",
  reported_temperature: "没量过",
  medications_taken: "没有",
  allergies: "没有",
  relevant_history: "没有",
  patient_goal: "整理信息",
};

export async function sessionSnapshot(page, id) {
  return page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
    id,
  );
}

export async function currentQuestion(page, id) {
  const snapshot = await sessionSnapshot(page, id);
  const replies = snapshot.events.filter(
    (e) => e.event_type === "turn.agent_reply",
  );
  return replies.at(-1)?.payload?.reply?.question_id ?? null;
}

export async function answerText(page, text) {
  await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(page.locator("#send-button")).toBeEnabled();
}

export async function chooseChoice(page, label) {
  await page.getByRole("button", { name: label, exact: true }).click();
  await expect(page.locator("#send-button")).toBeEnabled();
}

/** Answer questions until `isDone()` reports the target UI is on screen. */
export async function answerUntil(
  page,
  id,
  isDone,
  {
    answers = INTAKE_ANSWERS,
    choices = { trajectory: "变化不大" },
    max = 15,
  } = {},
) {
  for (let step = 0; step < max; step += 1) {
    if (await isDone()) return;
    const question = await currentQuestion(page, id);
    if (!question) return;
    // Red-flag warnings are compiled into questions with 有/没有/不确定.
    if (question.startsWith("warning_")) {
      await chooseChoice(page, "没有");
      continue;
    }
    if (choices[question]) {
      await chooseChoice(page, choices[question]);
      continue;
    }
    const text = answers[question];
    if (text === undefined) {
      throw new Error(`No synthetic answer for question "${question}"`);
    }
    await answerText(page, text);
  }
  if (await isDone()) return;
  throw new Error("Timed out answering intake questions");
}
