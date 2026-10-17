import { test, expect } from "@playwright/test";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

test("host state shows a waiting action and preserves answers and background work", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.clarifications = [
    {
      id: "question-1",
      turn_id: "turn-1",
      question: "希望如何查看结果？",
      status: "pending",
      options: ["生成图表", "保留数据表"],
    },
  ];
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "等待你的回答",
  );
  await page.locator("#execution-focus-respond").click();
  await expect(
    page.getByRole("button", { name: "生成图表", exact: true }),
  ).toBeFocused();
  await page.getByRole("button", { name: "生成图表", exact: true }).click();
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "正在处理任务",
  );
  await expect(page.locator("#execution-focus-respond")).toBeHidden();
  fixture.first.status = "completed";
  fixture.first.jobs = [{ id: "job-1", status: "running" }];
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "1 个后台任务运行中",
  );
  fixture.first.jobs = [];
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
});

test("file shortcuts open inert image, table and source previews without automatic reads", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  expect(fixture.previews()).toBe(0);
  const image = page.getByRole("button", {
    name: "快速查看：chart.png",
    exact: true,
  });
  await image.click();
  await expect(page.locator("#execution-preview img")).toBeVisible();
  await expect
    .poll(() =>
      page
        .locator("#execution-preview img")
        .evaluate((img) => img.naturalWidth),
    )
    .toBe(1);
  await expect(image).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#execution-file-browser")).not.toHaveAttribute(
    "open",
    "",
  );
  await page.locator("#execution-file-browser > summary").click();
  await expect(page.locator("#execution-quick-look")).not.toHaveAttribute(
    "open",
    "",
  );
  await expect(page.locator("#execution-preview img")).toBeVisible();
  await page.locator("#execution-quick-look > summary").click();
  await page
    .getByRole("button", { name: "快速查看：results.csv", exact: true })
    .click();
  await expect(page.locator("#execution-preview table")).toContainText("1250");
  await expect(image).toHaveAttribute("aria-pressed", "false");
  const source = page.getByRole("button", {
    name: "快速查看：notes<draft>.md",
    exact: true,
  });
  await source.focus();
  await page.waitForTimeout(1000);
  await expect(source).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#execution-preview")).toContainText("<img");
  expect(await page.evaluate(() => window.FOCUS_INJECTED)).toBeUndefined();
  fixture.first.status = "completed";
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  await page.locator("#execution-workspace").selectOption("focus-b");
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "已保存的工作区",
  );
  await expect(page.locator("#execution-quick-look")).toBeHidden();
  await expect(page.locator("#execution-preview")).not.toContainText("<img");
});

test("a completed turn with a failed tool remains visibly unresolved", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.status = "completed";
  fixture.first.events = [
    {
      sequence: 1,
      type: "tool.observed",
      turn_id: "turn-1",
      observation: {
        schema: "execution-observation/v1",
        session_id: "root",
        sdk_turn: 1,
        sdk_step: 1,
        call_id: "failed-call",
        observation_id: "failed-observation",
        turn_id: "turn-1",
        tool: "bash",
        state: "completed",
        exit_code: 7,
      },
    },
  ];
  fixture.first.sequence = 1;
  await expect(page.locator("#execution-focus-label")).toHaveText("结果待核对");
  await expect(page.locator("#execution-status")).toHaveText("本轮已结束");
  await expect(page.locator("#execution-outcome")).toBeVisible();
  await expect(page.locator(".execution-failure")).toContainText("7");
  fixture.first.storage_error = "工作目录暂不可读";
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "工作目录不可用",
  );
  await expect(page.locator("#execution-quick-look")).toBeHidden();
});

test("long results use a short excerpt, keep the full text and leave failures visible", async ({
  page,
}, info) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  const text =
    "已经保存结果，请查看文件。\n\n" +
    "这是需要时才展开的详细过程与记录。".repeat(45) +
    '<img src=x onerror="window.RESULT_INJECTED=1">';
  fixture.first.status = "completed";
  fixture.first.events = [
    {
      sequence: 1,
      type: "turn.completed",
      turn_id: "turn-1",
      final_response: text,
    },
    {
      sequence: 2,
      type: "tool.observed",
      turn_id: "turn-1",
      observation: {
        schema: "execution-observation/v1",
        session_id: "root",
        sdk_turn: 1,
        sdk_step: 1,
        call_id: "failed-long-result",
        observation_id: "failed-long-observation",
        turn_id: "turn-1",
        tool: "bash",
        state: "completed",
        exit_code: 7,
      },
    },
  ];
  fixture.first.sequence = 2;
  const card = page.locator(".execution-final");
  await expect(card.locator(".execution-response-preview")).toBeVisible();
  expect(
    (await card.locator(".execution-response-preview").textContent()).length,
  ).toBeLessThan(150);
  await expect(card.locator(".execution-response-preview")).not.toContainText(
    "详细过程",
  );
  await expect(card.locator(".execution-response-full")).toBeHidden();
  await expect(page.locator("#execution-outcome")).toBeVisible();
  await card.locator(".execution-response-preview").scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("condensed-results.png"),
    fullPage: true,
  });
  await card.locator("summary").click();
  await expect(card.locator(".execution-response-full")).toHaveText(text);
  await expect(card.locator(".execution-response-preview")).toBeHidden();
  await page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/execution/focus-a" && url.searchParams.has("after")
    );
  });
  await expect(card.locator("summary")).toHaveText("收起结果");
  await card.locator("summary").click();
  await expect(card.locator(".execution-response-full")).toBeHidden();
  await expect(page.locator("#execution-outcome")).toBeVisible();
  expect(await page.evaluate(() => window.RESULT_INJECTED)).toBeUndefined();
});

for (const [name, width, height] of [
  ["wide", 1440, 1080],
  ["mobile", 390, 844],
  ["small", 320, 640],
]) {
  test(`focused status and file shortcuts fit on ${name}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height });
    await installExecutionFocusFixture(page);
    await page.goto("/");
    await openFocusWorkspace(page);
    for (const theme of ["forest", "ocean", "iris", "sand", "slate", "night"]) {
      await page.evaluate(
        (value) => (document.documentElement.dataset.theme = value),
        theme,
      );
      const regions = await page
        .locator("#execution-focus, #execution-quick-files button")
        .evaluateAll((nodes) =>
          nodes.map((node) => {
            const box = node.getBoundingClientRect();
            return { width: box.width, left: box.left, right: box.right };
          }),
        );
      expect(
        regions.every(
          (box) => box.width > 30 && box.left >= 0 && box.right <= width,
        ),
      ).toBe(true);
      const overflow = await page.evaluate(() =>
        [...document.querySelectorAll("#view-execution *")]
          .filter((node) => node.getBoundingClientRect().right > innerWidth + 1)
          .map((node) => ({
            tag: node.tagName,
            id: node.id,
            cls: node.className,
            right: node.getBoundingClientRect().right,
          }))
          .slice(0, 8),
      );
      expect(overflow).toEqual([]);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: info.outputPath(`${name}-${theme}.png`),
        fullPage: true,
      });
    }
  });
}
