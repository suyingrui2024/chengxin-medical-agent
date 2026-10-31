import { test, expect } from "@playwright/test";
import {
  answerText,
  answerUntil,
  chooseChoice,
  currentQuestion,
} from "../fixtures/intake.mjs";
import { shotPath } from "../fixtures/shots.mjs";

async function start(page) {
  await page.goto("/");
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  return (await (await created).json()).session_id;
}
async function answer(page, text) {
  await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(page.locator("#send-button")).toBeEnabled();
}
async function history(
  page,
  id,
  chief = "咳嗽发烧两天",
  { stopAt = "severity" } = {},
) {
  await chooseChoice(page, "我自己");
  await chooseChoice(page, "男");
  await expect(page.locator("#active-question h2")).toContainText("多大");
  await answerText(page, "32岁");
  await answerText(page, chief);
  await chooseChoice(page, "都没有");
  // History order is discrimination-driven: answer whatever is asked until the
  // requested question is pending instead of assuming onset -> trajectory.
  await answerUntil(
    page,
    id,
    async () => (await currentQuestion(page, id)) === stopAt,
  );
}

test("visual choices show actual questions, keep history available and send one explicit answer", async ({
  page,
  request,
}) => {
  const id = await start(page);
  await expect(page.locator("#active-question .visual-choice")).toHaveCount(3);
  await expect(page.locator("#conversation-scroll")).toBeHidden();
  await page.screenshot({
    path: shotPath("14-visual-opening.png"),
    fullPage: true,
  });
  const chosen = page.waitForRequest((r) => r.url().endsWith("/messages"));
  await page.getByRole("button", { name: "我自己", exact: true }).focus();
  await page.keyboard.press("Enter");
  expect((await chosen).postDataJSON().choice_value).toBe("self");
  // Opening order is consult_for -> sex -> age.
  await expect(page.locator("#active-question h2")).toHaveText(
    "请问是男是女？",
  );
  await page.getByRole("button", { name: "男", exact: true }).click();
  await expect(page.locator("#active-question h2")).toContainText("多大");
  await page.getByRole("button", { name: "展开对话记录" }).click();
  await expect(page.locator(".message.user").last()).toHaveText("你男");
  await page.getByRole("button", { name: "返回当前问题" }).click();
  await answer(page, "不清楚");
  await expect(page.locator("#active-question h2")).toContainText(
    "没能确认年龄",
  );
  await expect(page.locator(".question-original")).toHaveCount(0);
  await answer(page, "32岁");
  await answer(page, "咳嗽");
  await expect(page.locator(".signal-grid")).toContainText("严重呼吸困难");
  await expect(page.locator(".signal-grid")).toContainText("持续压榨样胸痛");
  await expect(page.locator(".signal-grid")).toContainText(
    "叫不醒 / 意识明显异常",
  );
  const before = await (await request.get(`/api/sessions/${id}`)).json();
  expect(before.state.facts.emergency_screen).toBeUndefined();
  await page.screenshot({
    path: shotPath("15-visual-safety-screen.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "不确定", exact: true }).click();
  const after = await (await request.get(`/api/sessions/${id}`)).json();
  expect(after.state.facts.emergency_screen.assertion).toBe("unknown");
});

test("degree scale confirms one selected answer independently of the text draft", async ({
  page,
  request,
}) => {
  const id = await start(page);
  await history(page, id, undefined, { stopAt: "trajectory" });
  await expect(page.locator("#active-question .visual-choices")).toContainText(
    "在加重",
  );
  await page.screenshot({
    path: shotPath("16-visual-trajectory.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "变化不大", exact: true }).click();
  await expect(
    page.locator(".severity-options [aria-pressed='true']"),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "5 分", exact: true }).click();
  await expect(page.locator("#message-input")).toHaveValue("");
  const before = await (await request.get(`/api/sessions/${id}`)).json();
  expect(before.state.facts.severity).toBeUndefined();
  await page.screenshot({
    path: shotPath("17-visual-severity.png"),
    fullPage: true,
  });
  await page.locator("#message-input").fill("能正常活动");
  await expect(page.locator(".severity-value")).toHaveText("5 分");
  await expect(
    page.locator(".severity-options [aria-pressed='true']"),
  ).toHaveText("5");
  await page.getByRole("button", { name: "3 分", exact: true }).focus();
  await page.keyboard.press("Space");
  await page.getByRole("button", { name: "确认 3 分", exact: true }).click();
  await expect(page.locator("#message-input")).toHaveValue("能正常活动");
  await expect(page.locator(".severity-scale")).toHaveCount(0);
  const after = await (await request.get(`/api/sessions/${id}`)).json();
  expect(after.state.facts.severity.value).toBe(3);
});

test("body location uses a deliberate selection and confirmation, preserving self-report quality", async ({
  page,
  request,
}) => {
  const id = await start(page);
  await history(page, id, "手臂疼痛两天");
  await page.getByRole("button", { name: "3 分", exact: true }).click();
  await page.getByRole("button", { name: "确认 3 分", exact: true }).click();
  await answerUntil(page, id, () => page.locator(".body-map").isVisible());
  await expect(page.locator(".body-map")).toBeVisible();
  await expect(page.locator(".map-confirm")).toBeDisabled();
  await page
    .locator(".body-map")
    .getByRole("button", { name: "四肢", exact: true })
    .click();
  const before = await (await request.get(`/api/sessions/${id}`)).json();
  expect(before.state.facts.symptom_location).toBeUndefined();
  await page.screenshot({
    path: shotPath("18-visual-body-map.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "记录：四肢", exact: true }).click();
  await expect(page.locator(".body-map")).toHaveCount(0);
  const after = await (await request.get(`/api/sessions/${id}`)).json();
  expect(after.state.facts.symptom_location.value).toBe("四肢");
  expect(after.state.facts.symptom_location.source).toBe("patient");
  expect(after.state.facts.symptom_location.quality).toBe("not_assessed");
});

test("mobile visual screen and degree scale stay readable without overlapping targets", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await start(page);
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("button", { name: "男", exact: true }).click();
  await expect(page.locator("#active-question h2")).toContainText("多大");
  await answer(page, "32岁");
  await answer(page, "咳嗽");
  await expect(page.locator(".signal-grid li")).toHaveCount(6);
  await page.screenshot({
    path: shotPath("19-visual-mobile-screen.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "都没有", exact: true }).click();
  await answer(page, "昨天逐渐出现");
  await page.getByRole("button", { name: "变化不大", exact: true }).click();
  await page.getByRole("button", { name: "10 分", exact: true }).click();
  await expect(page.locator("#message-input")).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "确认 10 分", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const targets = await page
    .locator(".severity-options button")
    .evaluateAll((buttons) =>
      buttons.map((button) => ({
        width: button.getBoundingClientRect().width,
        height: button.getBoundingClientRect().height,
      })),
    );
  expect(
    targets.every((target) => target.width >= 44 && target.height >= 44),
  ).toBe(true);
  await page.screenshot({
    path: shotPath("20-visual-mobile-severity.png"),
    fullPage: true,
  });
});

test("custom and malformed visual components preserve question meaning and isolate unsafe data", async ({
  page,
}) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const { createRenderer } = await import("/structured-ui.js");
    const renderer = createRenderer();
    const host = document.createElement("section");
    host.id = "visual-contract-test";
    host.append(
      renderer.render({
        id: "clarify",
        type: "question",
        data: {
          question_id: "severity",
          canonical: false,
          prompt: "你说的无法活动，是现在发生的吗？",
          choices: [],
        },
      }),
    );
    host.append(
      renderer.render({
        id: "bad",
        type: "question",
        data: {
          question_id: "severity",
          canonical: true,
          prompt: "x",
          choices: [{ label: "<img src=x>", value: { bad: true } }],
        },
      }),
    );
    document.body.append(host);
  });
  await expect(page.locator("#visual-contract-test h2")).toHaveText(
    "你说的无法活动，是现在发生的吗？",
  );
  // The severity scale is an input control and follows the question id, not
  // `canonical`: a clarification keeps the control (only the title shortcut is
  // dropped). The malformed second block falls back instead.
  await expect(
    page.locator("#visual-contract-test .severity-scale"),
  ).toHaveCount(1);
  await expect(
    page.locator("#visual-contract-test .structured-fallback"),
  ).toHaveCount(1);
  await expect(page.locator("#visual-contract-test img")).toHaveCount(0);
});

test("the smallest desktop window keeps the actual response controls inside the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 680 });
  await start(page);
  const send = await page
    .getByRole("button", { name: "发送回答" })
    .boundingBox();
  expect(send.y + send.height).toBeLessThanOrEqual(680);
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("button", { name: "男", exact: true }).click();
  await answer(page, "32岁");
  await answer(page, "咳嗽");
  const choice = page.getByRole("button", { name: "不确定", exact: true });
  await choice.scrollIntoViewIfNeeded();
  await expect(choice).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: shotPath("22-compact-desktop.png"),
    fullPage: true,
  });
  await choice.click();
  await expect(page.locator("#send-button")).toBeEnabled();
});

// E2E-P2-5 回炉（W4 断言，W5 搬迁）：trajectory=intermittent 落账后，病例“已记录”
// 面板经 displayFact 渲染，须显示“阵发性”而非机器值 intermittent。
// 不调用 history()：该 helper 写于 sex-first 题序之前，点“我自己”后直接答
// “32岁”会被记到 sex 键、流程卡在年龄澄清步（基线既有陈旧问题，另作跟进，
// 本用例不改 helper 与既有用例）。本用例自带 sex-first 驱动路径。
test("intermittent trajectory renders as 阵发性 in recorded facts", async ({
  page,
  request,
}) => {
  const id = await start(page);
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("button", { name: "男", exact: true }).click();
  await answer(page, "32岁");
  await answer(page, "咳嗽发烧两天");
  await expect(
    page.getByRole("button", { name: "都没有", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "都没有", exact: true }).click();
  await answer(page, "两天前逐渐出现");
  await expect(page.locator("#active-question .visual-choices")).toContainText(
    "一阵一阵的",
  );
  await page.getByRole("button", { name: "一阵一阵的", exact: true }).click();
  const after = await (await request.get(`/api/sessions/${id}`)).json();
  expect(after.state.facts.trajectory.value).toBe("intermittent");
  const row = page.locator(".fact-row").filter({ hasText: "变化趋势" });
  await expect(row).toContainText("阵发性");
  await expect(row).not.toContainText("intermittent");
});
