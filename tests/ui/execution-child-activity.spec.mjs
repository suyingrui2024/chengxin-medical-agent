import { test, expect } from "@playwright/test";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
  verifyChildOnlyActivity,
} from "../fixtures/execution-focus.mjs";

for (const width of [1440, 320])
  test(`a completed root exposes and stops its remaining child at ${width}px`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = await installExecutionFocusFixture(page);
    await page.goto("/");
    await openFocusWorkspace(page);
    await verifyChildOnlyActivity(page, fixture, {
      shot: info.outputPath(`child-activity-${width}.png`),
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });

test("runtime activity clears without replaying historical child starts as live work", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.status = "completed";
  fixture.first.agents = [
    { session_id: "child", turn_id: fixture.first.turn_id, status: "running" },
  ];
  fixture.first.sequence++;
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "1 个子任务运行中",
  );
  await expect(page.locator("#execution-stop")).toBeEnabled();
  fixture.first.agents = [];
  fixture.first.events = [
    {
      sequence: 1,
      type: "runtime.event",
      notification: {
        method: "subagent.started",
        params: { childSessionId: "child", parentSessionId: fixture.first.id },
      },
    },
  ];
  fixture.first.sequence++;
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  await expect(page.locator("#execution-stop")).toBeDisabled();
});

test("an earlier child can receive an answer after the latest task has timed out", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.status = "cancelled";
  fixture.first.error = "time_budget_exhausted";
  fixture.first.turn_id = "new-short-turn";
  fixture.first.turns.push({
    id: "new-short-turn",
    status: "cancelled",
    model_calls: 1,
  });
  fixture.first.agents = [
    { session_id: "old-child", turn_id: "turn-1", status: "running" },
  ];
  fixture.first.clarifications = [
    {
      id: "still-live",
      session_id: "old-child",
      turn_id: "turn-1",
      question: "旧任务保留哪种格式？",
      options: ["CSV", "JSON"],
      status: "pending",
    },
  ];
  fixture.first.sequence++;
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "等待你的回答",
  );
  await expect(page.locator(".execution-question-heading")).toContainText(
    "较早子任务",
  );
  await page.getByRole("button", { name: "CSV", exact: true }).click();
  expect(fixture.answers).toEqual([]);
  await page.getByRole("button", { name: "发送回答 ↗", exact: true }).click();
  await expect.poll(() => fixture.answers.length).toBe(1);
  expect(fixture.answers[0]).toEqual({
    request_id: "still-live",
    turn_id: "turn-1",
    answer: "CSV",
  });
  expect(fixture.first.turn_id).toBe("new-short-turn");
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "1 个较早子任务运行中",
  );
});
