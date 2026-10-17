import { test, expect } from "@playwright/test";
import { reachSelfCheck } from "../fixtures/self-check-drafts.mjs";
import { degreeSnapshot } from "../fixtures/visual-answer-drafts.mjs";

test.beforeEach(async ({ request }) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
});

for (const [name, width, height] of [
  ["wide", 1440, 1080],
  ["short", 900, 648],
  ["mobile", 390, 844],
  ["small", 320, 640],
]) {
  test(`the current temperature action and controls fit on ${name}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    const id = await reachSelfCheck(page, "发热");
    const input = page.getByRole("textbox", { name: "输入你的描述" });
    await input.fill("测量时间还没填写");
    const before = await degreeSnapshot(page, id);
    const task = page.locator("#active-task");
    const captions = ["擦干腋窝", "探头放在腋窝中央", "等待完成，再记录"];
    for (let step = 0; step < 3; step++) {
      await expect(task.locator(".step-text")).toHaveText(captions[step]);
      await expect(
        task.locator(".step-track [aria-current='step']"),
      ).toHaveText(`${step + 1}${["准备", "放置", "读数"][step]}`);
      await expect(task.locator(".task-image")).toBeVisible();
      await expect(task.locator(".task-details")).not.toHaveAttribute(
        "open",
        "",
      );
      const geometry = await task.evaluate((pane) => {
        const bounds = pane.getBoundingClientRect();
        return [
          ".step-text",
          ".step-controls",
          ".task-image",
          ".task-stop",
        ].map((selector) => {
          const rect = pane.querySelector(selector).getBoundingClientRect();
          return {
            selector,
            visible:
              rect.top >= bounds.top &&
              rect.bottom <= bounds.bottom &&
              rect.left >= 0 &&
              rect.right <= innerWidth,
          };
        });
      });
      expect(geometry.filter((item) => !item.visible)).toEqual([]);
      await page.screenshot({
        path: info.outputPath(`${name}-step-${step + 1}.png`),
      });
      if (step < 2)
        await task.getByRole("button", { name: "下一步", exact: true }).click();
    }
    await task.locator(".task-details > summary").click();
    await expect(task.locator(".step-full-list")).toContainText(
      "记录数值、腋温和测量时间",
    );
    await expect(task.locator(".task-details")).toContainText(
      "一次体温不能排除病情变化",
    );
    await expect(task.locator(".task-stop")).toContainText("操作引起明显不适");
    await task.locator(".task-details > summary").click();
    await task.getByRole("button", { name: "填写结果", exact: true }).click();
    await expect(input).toBeFocused();
    await expect(input).toHaveValue("测量时间还没填写");
    expect(await degreeSnapshot(page, id)).toEqual(before);
  });
}

test("body diagram clicks and keyboard selection keep a draft until explicit confirmation", async ({
  page,
}) => {
  await page.goto("/");
  const id = await reachSelfCheck(page);
  const before = await degreeSnapshot(page, id);
  await page
    .getByRole("button", { name: "在图上选择胸部", exact: true })
    .click();
  await expect(
    page.locator('.body-pin[data-location="chest"]'),
  ).toHaveAttribute("aria-pressed", "true");
  const abdomen = page.getByRole("button", {
    name: "在图上选择腹部",
    exact: true,
  });
  await abdomen.focus();
  await page.keyboard.press("Space");
  await expect(
    page.getByRole("button", { name: "记录：腹部", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "背/腰部", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "在图上选择腹部", exact: true }),
  ).toBeHidden();
  await expect(
    page.getByRole("button", { name: "在图上选择背/腰部", exact: true }),
  ).toBeVisible();
  expect(await degreeSnapshot(page, id)).toEqual(before);
  const inert = await page.evaluate(
    async (task) => {
      const { createRenderer } = await import("/structured-ui.js");
      let changes = 0;
      const card = createRenderer().render(
        { id: "readonly", type: "self_check", data: task },
        {
          sessionId: "readonly",
          readOnly: true,
          onChoose: () => changes++,
          onTaskDraft: () => changes++,
        },
      );
      card.querySelectorAll(".body-region-target").forEach((target) => {
        target.dispatchEvent(new MouseEvent("click"));
        target.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
      });
      return {
        changes,
        disabled: [...card.querySelectorAll(".body-region-target")].every(
          (target) => target.getAttribute("aria-disabled") === "true",
        ),
      };
    },
    before.events.filter((e) => e.event_type === "turn.agent_reply").at(-1)
      .payload.reply.task,
  );
  expect(inert).toEqual({ changes: 0, disabled: true });
});

test("a different capability instruction is never replaced by the compact vocabulary", async ({
  page,
}) => {
  await page.goto("/");
  const id = await reachSelfCheck(page, "发热");
  const before = await degreeSnapshot(page, id);
  const rendered = await page.evaluate(
    async (task) => {
      const { createRenderer } = await import("/structured-ui.js");
      const card = createRenderer().render(
        {
          id: "different",
          type: "self_check",
          data: {
            ...task,
            steps: ["来自其他版本的完整操作说明", ...task.steps.slice(1)],
          },
        },
        { sessionId: "different", onAnswer: () => {} },
      );
      return {
        compact: card.classList.contains("illustrated-task"),
        instruction: card.querySelector(".step-text").textContent,
      };
    },
    before.events.filter((e) => e.event_type === "turn.agent_reply").at(-1)
      .payload.reply.task,
  );
  expect(rendered).toEqual({
    compact: false,
    instruction: "来自其他版本的完整操作说明",
  });
});
