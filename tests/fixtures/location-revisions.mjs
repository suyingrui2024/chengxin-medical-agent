import { expect } from "@playwright/test";
import { verifyLocationAnswers } from "./location-answers.mjs";
import { degreeSnapshot } from "./visual-answer-drafts.mjs";

export async function verifyLocationRevision(page, model, { shot } = {}) {
  const { sessionId, result } = await verifyLocationAnswers(page, model);
  const pane = page.locator("#active-task");
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  const start = async () => {
    const control = page.locator("#fact-list .location-revision-start");
    if (!(await control.isVisible()))
      await page.locator("#toggle-facts").click();
    await expect(control).toBeEnabled();
    const box = await control.boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);
    await control.click();
    await expect(page.locator("#facts-panel")).not.toHaveClass(/open/);
    await expect(pane.locator(".location-answer-review")).toBeVisible();
    await expect(pane.locator(".location-answer-source summary")).toHaveText(
      "原记录 · 待核对",
    );
  };
  const chooseLeft = async () => {
    await pane
      .getByRole("button", { name: "左右 · 右侧", exact: true })
      .click();
    await pane.getByRole("button", { name: "左侧", exact: true }).click();
    await pane.getByRole("button", { name: "下一步", exact: true }).click();
    await pane.getByRole("button", { name: "下一步", exact: true }).click();
    await expect(
      pane.getByRole("button", { name: "左右 · 左侧", exact: true }),
    ).toBeVisible();
  };
  await start();
  await chooseLeft();
  await pane
    .getByRole("button", { name: "核对并记录位置", exact: true })
    .evaluate((node) => (window.cancelledLocationConfirm = node));
  await page
    .getByRole("button", { name: "取消修改，保留原记录", exact: true })
    .click();
  await expect(page.locator("#active-question h2")).toBeVisible();
  const cancelled = await degreeSnapshot(page, sessionId);
  expect(cancelled.state.facts.symptom_location).toEqual(result);
  await page.evaluate(() => {
    window.cancelledLocationConfirm.disabled = false;
    window.cancelledLocationConfirm.click();
  });
  expect((await degreeSnapshot(page, sessionId)).events).toEqual(
    cancelled.events,
  );
  await start();
  await chooseLeft();
  await expect(input).toHaveValue("这段用药补充还没有发送。");
  const visible = await page.evaluate(() =>
    [
      "#active-task h3",
      ".location-answer-review",
      ".location-answer-final-actions",
      "#message-input",
      "#urgent-help",
    ].map((selector) => {
      const r = document.querySelector(selector).getBoundingClientRect();
      return {
        selector,
        visible:
          r.top >= 0 &&
          r.bottom <= innerHeight &&
          r.left >= 0 &&
          r.right <= innerWidth,
      };
    }),
  );
  expect(visible.filter((x) => !x.visible)).toEqual([]);
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  await pane
    .getByRole("button", { name: "核对并记录位置", exact: true })
    .click();
  await expect(page.locator("#active-question h2")).toBeVisible();
  const final = await degreeSnapshot(page, sessionId);
  const current = final.state.facts.symptom_location;
  expect(current.supersedes).toBe(result.fact_id);
  expect(current.structured_input.revision_of).toBe(result.fact_id);
  expect(current.structured_input.answers.side.value).toBe("left");
  expect(current.quality).toBe("not_assessed");
  expect(final.state.active_question).toBe("onset");
  expect(
    final.state.history.filter((f) => f.key === "symptom_location"),
  ).toHaveLength(2);
  // One more call than scripted turns: the engine rejected a proposal and
  // nextCommand answered it without advancing the script.
  expect(model.requests).toHaveLength(5);
  await expect(input).toHaveValue("这段用药补充还没有发送。");
}
