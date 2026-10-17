import { test, expect } from "@playwright/test";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

async function start(page) {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.clarifications = [
    {
      id: "draft-1",
      turn_id: "turn-1",
      status: "pending",
      question: "希望如何查看结果？",
      options: ["图表", "数据表"],
    },
  ];
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "等待你的回答",
  );
  return fixture;
}
const poll = (page) =>
  page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === "/api/execution/focus-a" && url.searchParams.has("after")
    );
  });
test("choose, revise and edit a draft without submitting until Send, retaining it across host polling", async ({
  page,
}, info) => {
  const fixture = await start(page);
  const card = page.locator(".execution-question");
  const send = card.getByRole("button", { name: "发送回答 ↗", exact: true });
  await expect(send).toBeDisabled();
  await card.getByRole("button", { name: "图表", exact: true }).click();
  await card.getByRole("button", { name: "数据表", exact: true }).click();
  await expect(
    card.getByRole("button", { name: "数据表", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await poll(page);
  expect(fixture.answers).toEqual([]);
  await card.getByRole("button", { name: "自行说明", exact: true }).click();
  const input = card.getByLabel("补充回答");
  await expect(input).toHaveValue("数据表");
  await input.fill("数据表，保留原始顺序");
  await poll(page);
  await expect(input).toHaveValue("数据表，保留原始顺序");
  await card.screenshot({ path: info.outputPath("editable-answer.png") });
  await send.click();
  await expect.poll(() => fixture.answers.length).toBe(1);
  expect(fixture.answers[0]).toEqual({
    request_id: "draft-1",
    turn_id: "turn-1",
    answer: "数据表，保留原始顺序",
  });
  await expect(card).toContainText("已核对");
});

test("failed sending preserves the editable draft and a retry submits exactly once", async ({
  page,
}) => {
  const fixture = await start(page);
  await page.getByRole("button", { name: "图表", exact: true }).click();
  await page.route("**/api/execution/focus-a/answer", (route) =>
    route.fulfill({ status: 503, json: { error: "稍后重试" } }),
  );
  const send = page.getByRole("button", { name: "发送回答 ↗", exact: true });
  await send.click();
  await expect(page.locator(".execution-question-error")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "图表", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(fixture.answers).toEqual([]);
  await page.unroute("**/api/execution/focus-a/answer");
  await send.click();
  await expect.poll(() => fixture.answers.length).toBe(1);
  expect(fixture.answers[0].answer).toBe("图表");
});

test("changed or removed questions and workspace switches never reuse an old answer draft", async ({
  page,
}) => {
  const fixture = await start(page);
  await page.getByRole("button", { name: "图表", exact: true }).click();
  fixture.first.clarifications[0].options = ["保存文字"];
  await expect(
    page.getByRole("button", { name: "保存文字", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "发送回答 ↗", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "保存文字", exact: true }).click();
  const question = fixture.first.clarifications[0];
  fixture.first.clarifications = [];
  fixture.first.status = "completed";
  await expect(page.locator(".execution-question")).toHaveCount(0);
  await expect(page.locator("#execution-workspace")).toBeEnabled();
  await page.locator("#execution-workspace").selectOption("focus-b");
  await expect(page.locator("#execution-clarifications")).toBeHidden();
  fixture.first.clarifications = [question];
  await page.locator("#execution-workspace").selectOption("focus-a");
  await expect(
    page.getByRole("button", { name: "发送回答 ↗", exact: true }),
  ).toBeDisabled();
  fixture.first.clarifications = [];
  await expect(page.locator(".execution-question")).toHaveCount(0);
  expect(fixture.answers).toEqual([]);
});

for (const width of [320, 390]) {
  test(`editable answers remain within the screen across six themes at ${width}px`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 844 });
    const fixture = await start(page);
    const card = page.locator(".execution-question");
    await card.getByRole("button", { name: "图表", exact: true }).click();
    await card.getByRole("button", { name: "自行说明", exact: true }).click();
    await card
      .getByLabel("补充回答")
      .fill("显示图表，并保留完整原始数据。".repeat(4));
    for (const theme of ["forest", "ocean", "iris", "sand", "slate", "night"]) {
      await page.evaluate(
        (value) => (document.documentElement.dataset.theme = value),
        theme,
      );
      const bounds = await card
        .locator("button, textarea")
        .evaluateAll((nodes) =>
          nodes
            .filter((node) => node.getBoundingClientRect().width)
            .map((node) => {
              const rect = node.getBoundingClientRect();
              return { left: rect.left, right: rect.right };
            }),
        );
      expect(
        bounds.every((rect) => rect.left >= 0 && rect.right <= width),
      ).toBe(true);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await card.screenshot({ path: info.outputPath(`answer-${theme}.png`) });
    }
    expect(fixture.answers).toEqual([]);
  });
}
