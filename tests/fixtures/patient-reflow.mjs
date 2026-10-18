import { expect } from "@playwright/test";
import { reachSelfCheck } from "./self-check-drafts.mjs";
import { degreeSnapshot } from "./visual-answer-drafts.mjs";

// Scroll through the actual page, then reject clipping by any nested pane.
export async function expectReachable(locator) {
  await expect(locator).toBeVisible();
  await locator.scrollIntoViewIfNeeded();
  await expect
    .poll(() =>
      locator.evaluate((node) => {
        const b = node.getBoundingClientRect(),
          issues = [];
        if (
          b.top < -1 ||
          b.bottom > innerHeight + 1 ||
          b.left < -1 ||
          b.right > innerWidth + 1
        )
          issues.push("viewport");
        const hit = document.elementFromPoint(
          (b.left + b.right) / 2,
          (b.top + b.bottom) / 2,
        );
        if (
          !hit ||
          (!node.contains(hit) &&
            hit !== node.closest("button,a,[role=button]"))
        )
          issues.push(`covered by ${hit?.id || hit?.tagName || "unknown"}`);
        for (
          let parent = node.parentElement;
          parent && parent !== document.documentElement;
          parent = parent.parentElement
        ) {
          const style = getComputedStyle(parent),
            p = parent.getBoundingClientRect();
          if (
            ["auto", "scroll", "hidden", "clip"].includes(style.overflowY) &&
            (b.top < p.top - 1 || b.bottom > p.bottom + 1)
          )
            issues.push(parent.id || parent.className || parent.tagName);
          if (parent.matches(":modal")) break;
        }
        return issues;
      }),
    )
    .toEqual([]);
}

export async function verifyTemperatureReflow(page, id, { shot } = {}) {
  const before = await degreeSnapshot(page, id);
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  await input.fill("放大显示时保留这份还没填写的读数");
  const task = page.locator("#active-task");
  const pane = await task.evaluate((node) => ({
    height: node.clientHeight,
    content: node.scrollHeight,
    overflow: getComputedStyle(node).overflowY,
  }));
  expect(pane.overflow).toBe("visible");
  expect(pane.height).toBeGreaterThanOrEqual(pane.content - 1);
  const captions = ["擦干腋窝", "探头放在腋窝中央", "等待完成，再记录"];
  for (let step = 0; step < 3; step++) {
    await expect(task.locator(".step-text")).toHaveText(captions[step]);
    for (const selector of [
      ".task-image",
      ".step-text",
      ".step-controls",
      ".task-stop",
    ])
      await expectReachable(task.locator(selector));
    await expect(task.locator(".task-stop")).toContainText("操作引起明显不适");
    if (step === 0) {
      const zoom = task.getByRole("button", { name: "放大图示", exact: true });
      await zoom.click();
      const dialog = task.locator(".illustration-dialog[open]");
      await expectReachable(dialog.locator(".illustration-large"));
      await expectReachable(
        dialog.getByRole("button", { name: "关闭大图", exact: true }),
      );
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(zoom).toBeFocused();
    }
    if (shot && step === 0) {
      await task.locator(".task-stage").scrollIntoViewIfNeeded();
      await shot();
    }
    if (step < 2)
      await task.getByRole("button", { name: "下一步", exact: true }).click();
  }
  await task.getByRole("button", { name: "填写结果", exact: true }).click();
  await expect(input).toBeFocused();
  await expectReachable(input);
  await expect(input).toHaveValue("放大显示时保留这份还没填写的读数");
  expect(await degreeSnapshot(page, id)).toEqual(before);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  // Leave the current task at its first step for another zoom setting.
  await task.getByRole("button", { name: "上一步", exact: true }).click();
  await task.getByRole("button", { name: "上一步", exact: true }).click();
  return pane;
}

export async function verifyNativePatientZoom(page, setZoom, { shot } = {}) {
  // This probe is the last step of native acceptance; select the local demo
  // through the application's controls and retain its private native document.
  await setZoom(1);
  await page.getByLabel("界面模式").selectOption("developer");
  await page
    .getByRole("button", { name: "模型与运行配置", exact: true })
    .click();
  await page.getByRole("radio", { name: /离线演示/ }).check();
  await page.locator("#save-settings").click();
  await expect(page.locator("#settings-dialog")).not.toHaveAttribute(
    "open",
    "",
  );
  await page.getByRole("button", { name: "问诊", exact: true }).click();
  await page.getByLabel("界面模式").selectOption("patient");
  try {
    await setZoom(2);
    const id = await reachSelfCheck(page, "发热");
    const heading = page.locator("#active-task h3");
    await expect(heading).toBeFocused();
    const bounds = await heading.boundingBox();
    expect(bounds.y).toBeGreaterThanOrEqual(-1);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(
      await page.evaluate(() => innerHeight + 1),
    );
    const settings = [];
    for (const factor of [1.25, 1.5, 1.75, 2]) {
      const actual = await setZoom(factor);
      expect(actual).toBeCloseTo(factor, 5);
      await expect
        .poll(() => page.evaluate(() => innerHeight))
        .toBeLessThanOrEqual(560);
      const pane = await verifyTemperatureReflow(page, id, {
        shot:
          shot && (factor === 1.25 || factor === 2)
            ? () => shot(factor)
            : undefined,
      });
      settings.push({ zoom: actual, pane });
    }
    return {
      passed: true,
      native_webcontents_zoom: true,
      settings,
      synthetic_demo: true,
      external_model_calls: 0,
    };
  } finally {
    await setZoom(1);
  }
}
