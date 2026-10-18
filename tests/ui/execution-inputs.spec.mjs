import { test, expect } from "@playwright/test";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

test("an unverified input scope cannot show a passing check even with exit zero and a current end snapshot", async ({
  page,
}, info) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  const value = {
    schema: "execution-observation/v1",
    session_id: "root",
    sdk_turn: 1,
    sdk_step: 1,
    call_id: "input-check",
    observation_id: "input-observation",
    turn_id: "turn-1",
    tool: "delivery_check",
    state: "completed",
    exit_code: 0,
    check_key: "a".repeat(64),
    check_label: "源文件检查",
    check_required: true,
    check_inputs: [],
    check_snapshot: {
      schema: "workspace-checkpoint/v1",
      paths: ["."],
      sha256: "a".repeat(64),
      complete: true,
      files: 1,
      ignored_directories: [],
      symlink_targets_followed: false,
    },
  };
  fixture.first.status = "completed";
  fixture.first.sequence = 2;
  fixture.first.events = [
    {
      type: "tool.observed",
      sequence: 1,
      turn_id: "turn-1",
      observation: value,
    },
    {
      type: "delivery.freshness",
      sequence: 2,
      turn_id: "turn-1",
      session_id: "root",
      call_id: "input-check",
      check_key: value.check_key,
      freshness: "current",
    },
  ];
  await expect(page.locator(".execution-check-chip")).toContainText(
    "输入待复验",
  );
  await expect(page.locator(".execution-check-chip")).not.toHaveClass(
    /check-succeeded/,
  );
  await expect(page.locator(".execution-failure")).toContainText("命令退出 0");
  await expect(page.locator("#execution-focus-label")).toHaveText("结果待核对");
  await page
    .locator(".execution-check-chip")
    .screenshot({ path: info.outputPath("input-unverified.png") });
  fixture.first.sequence = 4;
  fixture.first.events.push({
    type: "tool.observed",
    sequence: 3,
    turn_id: "turn-1",
    observation: {
      ...value,
      call_id: "input-recheck",
      observation_id: "input-rechecked",
      check_inputs: ["source.txt"],
    },
  });
  fixture.first.events.push({
    type: "delivery.freshness",
    sequence: 4,
    turn_id: "turn-1",
    session_id: "root",
    call_id: "input-recheck",
    check_key: value.check_key,
    freshness: "current",
  });
  await expect(page.locator(".execution-check-chip")).toContainText("退出 0");
  await expect(page.locator(".execution-check-chip")).toHaveClass(
    /check-succeeded/,
  );
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
});
