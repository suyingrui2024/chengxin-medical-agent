import { expect } from "@playwright/test";
import { degreeSnapshot } from "./visual-answer-drafts.mjs";

export async function verifyLocationLayout(page, id, { shot } = {}) {
  const before = await degreeSnapshot(page, id);
  const states = [];
  const targets = await page
    .locator("#active-task .body-pin")
    .evaluateAll((nodes) =>
      nodes.map((node) => {
        const r = node.getBoundingClientRect();
        return {
          text: node.textContent,
          x: r.x,
          y: r.y,
          width: r.width,
          height: r.height,
        };
      }),
    );
  const overlaps = [];
  for (let i = 0; i < targets.length; i++)
    for (let j = i + 1; j < targets.length; j++) {
      const a = targets[i],
        b = targets[j];
      if (
        Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > 0 &&
        Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > 0
      )
        overlaps.push([a.text, b.text]);
    }
  expect(targets).toHaveLength(5);
  expect(targets.every((t) => t.width >= 44 && t.height >= 44)).toBe(true);
  expect(overlaps).toEqual([]);
  const alternatives = await page
    .locator("#quick-replies button:visible")
    .evaluateAll((nodes) =>
      nodes.map((node) => {
        const r = node.getBoundingClientRect();
        return { width: r.width, height: r.height, bottom: r.bottom };
      }),
    );
  expect(alternatives).toHaveLength(2);
  expect(
    alternatives.every((item) => item.width >= 44 && item.height >= 44),
  ).toBe(true);
  for (const [label, value] of [
    ["头/面部", "head"],
    ["胸部", "chest"],
    ["腹部", "abdomen"],
    ["背/腰部", "back"],
    ["四肢", "limbs"],
  ]) {
    const choice = page
      .locator("#active-task .body-map")
      .getByRole("button", { name: label, exact: true });
    if (value === "chest" || value === "abdomen") await choice.click();
    else {
      await choice.focus();
      await page.keyboard.press("Space");
    }
    await expect(choice).toHaveAttribute("aria-pressed", "true");
    await expect(
      page.locator("#active-task .body-pin[aria-pressed='true']"),
    ).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: `记录：${label}`, exact: true }),
    ).toBeEnabled();
    await expect(page.locator(".body-silhouette")).toHaveCount(1);
    await expect(page.locator(".body-selection-marker")).toHaveAttribute(
      "data-location",
      value,
    );
    await expect(page.locator(".body-map-orientations")).toHaveText(
      value === "back"
        ? "背面示意"
        : value === "limbs"
          ? "手臂／腿示意"
          : "正面示意",
    );
    await expect(
      page.locator(".body-selection-marker .body-region"),
    ).toHaveCount(value === "limbs" ? 4 : 1);
    const geometry = await page.evaluate(() => {
      const pane = document
        .querySelector("#active-task")
        .getBoundingClientRect();
      const selectors = [
        "#active-task h3",
        ".body-map-canvas",
        ".body-map-options",
        ".map-confirm",
        "#message-input",
        "#urgent-help",
      ];
      return selectors.map((selector) => {
        const r = document.querySelector(selector).getBoundingClientRect();
        const scoped =
          selector.startsWith(".") || selector === "#active-task h3";
        return {
          selector,
          bounds: {
            top: r.top,
            bottom: r.bottom,
            left: r.left,
            right: r.right,
          },
          viewport: { width: innerWidth, height: innerHeight },
          pane: { top: pane.top, bottom: pane.bottom },
          visible:
            r.top >= 0 &&
            r.bottom <= innerHeight &&
            r.left >= 0 &&
            r.right <= innerWidth &&
            (!scoped || (r.top >= pane.top && r.bottom <= pane.bottom)),
        };
      });
    });
    expect(geometry.filter((item) => !item.visible)).toEqual([]);
    states.push({ value, geometry });
  }
  const after = await degreeSnapshot(page, id);
  expect(after.events).toEqual(before.events);
  expect(after.state.facts).toEqual(before.state.facts);
  if (shot) await page.screenshot({ path: shot, fullPage: true });
  return { targets, overlaps, alternatives, states };
}
