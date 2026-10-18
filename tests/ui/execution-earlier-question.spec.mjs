import { test, expect } from "@playwright/test";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

async function earlierQuestion(page, { shot } = {}) {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.turn_id = "turn-2";
  fixture.first.turns.push({ id: "turn-2", model_calls: 1 });
  fixture.first.clarifications = [
    {
      id: "earlier-child-question",
      session_id: "child",
      turn_id: "turn-1",
      question: "原任务保留哪种格式？",
      options: ["CSV", "JSON"],
      status: "pending",
    },
  ];
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "等待你的回答",
  );
  await expect(page.locator(".execution-question-heading")).toContainText(
    "较早子任务",
  );
  await page.locator("#execution-focus-respond").click();
  await expect(
    page.getByRole("button", { name: "CSV", exact: true }),
  ).toBeFocused();
  await page.getByRole("button", { name: "JSON", exact: true }).click();
  expect(fixture.answers).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  if (shot)
    await page.locator(".execution-question").screenshot({ path: shot });
  await page.getByRole("button", { name: "发送回答 ↗", exact: true }).click();
  await expect.poll(() => fixture.answers.length).toBe(1);
  expect(fixture.answers[0]).toEqual({
    request_id: "earlier-child-question",
    turn_id: "turn-1",
    answer: "JSON",
  });
  expect(fixture.first.turn_id).toBe("turn-2");
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "正在处理任务",
  );
}

test("a live earlier child question remains answerable in its original turn", async ({
  page,
}) => {
  await earlierQuestion(page);
});

test("an earlier child question stays readable and answerable at 320px", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await earlierQuestion(page, {
    shot: info.outputPath("earlier-question-320.png"),
  });
});
