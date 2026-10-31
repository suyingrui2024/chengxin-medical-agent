import { expect } from "@playwright/test";
import { verifyLocationAnswers } from "./location-answers.mjs";
import { degreeSnapshot } from "./visual-answer-drafts.mjs";

export async function verifyLocationRevision(page, model, { shot } = {}) {
  const { sessionId, result } = await verifyLocationAnswers(page, model);
  const pane = page.locator("#active-task");
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  const start = async () => {
    const panel = page.locator("#facts-panel");
    const control = page.locator("#fact-list .location-revision-start");
    // The drawer is `position: fixed` and slides in from the right; closed
    // it is only translated off screen, so `isVisible()` still reports true
    // on wide viewports and the click below would wait for a control that
    // never scrolls into view. Open it by state, not by visibility.
    if (!(await panel.evaluate((node) => node.classList.contains("open"))))
      await page.locator("#toggle-facts").click();
    await expect(panel).toHaveClass(/open/);
    await expect(control).toBeEnabled();
    // Measure the box rather than `toBeInViewport`: the drawer's slide-in is
    // a CSS transform, and the IntersectionObserver behind that matcher
    // reports the untransformed (off-screen) box, so it never passes here.
    await expect
      .poll(async () => {
        const box = await control.boundingBox();
        if (!box) return null;
        const viewport = page.viewportSize();
        return box.x >= 0 &&
          box.y >= 0 &&
          box.x + box.width <= viewport.width &&
          box.y + box.height <= viewport.height
          ? Math.round(box.height)
          : null;
      })
      .toBeGreaterThanOrEqual(44);
    await control.click();
    await expect(panel).not.toHaveClass(/open/);
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
  // Four scripted turns, plus at most two compliance replies (ordering
  // or a triage field). Bounded rather than exact: which gate fires
  // depends on the case, but a runaway loop still blows past this.
  expect(model.requests.length).toBeGreaterThanOrEqual(4);
  expect(model.requests.length).toBeLessThanOrEqual(6);
  await expect(input).toHaveValue("这段用药补充还没有发送。");
}
