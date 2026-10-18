import { test, expect } from "@playwright/test";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

for (const [status, restored] of [
  ["failed", false],
  ["failed", true],
  ["cancelled", true],
  ["interrupted", true],
]) {
  test(`${status} ${restored ? "saved" : "live"} turn keeps its explanation without claiming success`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const fixture = await installExecutionFocusFixture(page);
    await page.goto("/");
    await openFocusWorkspace(page);
    const text =
      "已保存实现；回归检查未通过，需要继续修复。\n\n" +
      "保留这段尚未完成的工作说明。".repeat(35) +
      '<img src=x onerror="window.INCOMPLETE_INJECTED=1">';
    fixture.first.status = status;
    fixture.first.restored = restored;
    fixture.first.error = status === "failed" ? "delivery_checks_failed" : null;
    fixture.first.events = [
      {
        sequence: 1,
        type: `turn.${status}`,
        turn_id: "turn-1",
        error_type: fixture.first.error,
        final_response: text,
        restored,
      },
    ];
    fixture.first.sequence = 1;
    await expect(page.locator("#execution-focus-label")).toHaveText(
      status === "failed"
        ? "本轮尚未完成"
        : status === "cancelled"
          ? "任务已停止"
          : "上次任务已中断",
    );
    const explanation = page.locator(".execution-incomplete-response");
    await expect(explanation.locator("summary")).toHaveText(
      restored ? "查看历史说明" : "查看本轮说明",
    );
    await expect(explanation.locator("pre")).toBeHidden();
    await explanation.locator("summary").click();
    await expect(explanation.locator("pre")).toHaveText(text);
    await expect(page.locator(".execution-final")).toHaveCount(0);
    expect(
      await page.evaluate(() => window.INCOMPLETE_INJECTED),
    ).toBeUndefined();
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
    await page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === "/api/execution/focus-a" &&
        url.searchParams.has("after")
      );
    });
    await expect(explanation).toHaveAttribute("open", "");
    await expect(explanation).toHaveCount(1);
    if (!restored)
      await page.screenshot({
        path: info.outputPath("incomplete-explanation.png"),
      });
    // A later turn must not inherit the old explanation as its own result.
    fixture.first.turn_id = "turn-2";
    fixture.first.turns.push({ id: "turn-2", model_calls: 1 });
    fixture.first.status = "running";
    fixture.first.error = null;
    await expect(page.locator("#execution-focus-label")).toHaveText(
      "正在处理任务",
    );
    await expect(explanation).toHaveCount(1);
    await expect(page.locator(".execution-final")).toHaveCount(0);
    expect(fixture.answers).toEqual([]);
  });
}
