import { test, expect } from "@playwright/test";
import { createLocationAnswersModel } from "../fixtures/location-answers.mjs";
import { verifyLocationRevision } from "../fixtures/location-revisions.mjs";
import { verifyLocationAnswers } from "../fixtures/location-answers.mjs";
import { degreeSnapshot } from "../fixtures/visual-answer-drafts.mjs";
import { shotPath } from "../fixtures/shots.mjs";

for (const [label, width, height] of [
  ["wide", 1440, 1080],
  ["mobile", 390, 844],
  ["short", 900, 648],
]) {
  test(`confirmed location revision cancels safely and resumes the question on ${label}`, async ({
    page,
    request,
  }) => {
    const model = await createLocationAnswersModel();
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "location-revision-fixture",
            },
          })
        ).ok(),
      ).toBe(true);
      await page.setViewportSize({ width, height });
      await page.goto("/");
      await verifyLocationRevision(page, model, {
        shot: shotPath(`78-${label}-location-revision.png`),
      });
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
}

test("location revision returns an unsent severity selection to the original question", async ({
  page,
  request,
}) => {
  const model = await createLocationAnswersModel(false, {
    nextQuestion: "severity",
    // The ranker owns the order, so the scripted severity question only
    // lands after the deep-dive pair it ranks first. The fixture records
    // what the patient states, keyed by the question actually being
    // asked, so this stays correct if the ranking changes again.
    answers: {
      onset: ["onset", "两天前逐渐出现"],
      trajectory: ["trajectory", "stable"],
    },
  });
  try {
    await request.post("/api/settings", {
      data: {
        backend: "compatible",
        base_url: model.endpoint,
        model: "fixture",
        api_key: "location-revision-degree",
      },
    });
    await page.setViewportSize({ width: 900, height: 648 });
    await page.goto("/");
    const { sessionId } = await verifyLocationAnswers(page, model);
    // Walk the engine's own order until severity is the pending question.
    // The state, not the DOM, is the signal: the card renders its controls
    // and its heading in separate passes, so reading the heading can catch
    // a half-drawn step and send one answer too many.
    const input = page.getByRole("textbox", { name: "输入你的描述" });
    const five = page.getByRole("button", { name: "5 分", exact: true });
    const pending = async () =>
      (await degreeSnapshot(page, sessionId)).state.active_question;
    for (
      let step = 0;
      step < 4 && (await pending()) !== "severity";
      step += 1
    ) {
      await input.fill("两天前逐渐出现，变化不大");
      await page.getByRole("button", { name: "发送回答" }).click();
      await expect(page.locator("#busy-indicator")).toBeHidden();
    }
    expect(await pending()).toBe("severity");
    await expect(five).toBeVisible();
    const draft = "这段用药补充还没有发送。";
    await input.fill(draft);
    await five.click();
    await expect(
      page.getByRole("button", { name: "确认 5 分", exact: true }),
    ).toBeEnabled();
    const calls = model.requests.length;
    await page.locator("#toggle-facts").click();
    await page.locator("#fact-list .location-revision-start").click();
    await expect(page.locator(".location-answer-review")).toBeVisible();
    await page
      .getByRole("button", { name: "取消修改，保留原记录", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "确认 5 分", exact: true }),
    ).toBeEnabled();
    await expect(
      page.locator('.severity-options [aria-pressed="true"]'),
    ).toHaveText("5");
    await expect(input).toHaveValue(draft);
    // Cancelling a revision is a local rollback: it must not cost a turn.
    expect(model.requests).toHaveLength(calls);
  } finally {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await model.close();
  }
});
