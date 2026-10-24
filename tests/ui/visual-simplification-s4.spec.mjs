import { expect, test } from "@playwright/test";

// S4 设置分层验收：患者模式下设置页默认可见区为外观/模型服务/
// 界面与数据 3 区，子智能体区默认不渲染（无指标文本、无回退按钮）；
// 点开“高级”后子智能体区出现且可操作；版本行只在高级区内。
test("S4 settings show three patient sections, subagents behind advanced", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.locator("#view-settings")).toBeVisible();

  const before = await page.evaluate(() => {
    const rects = (sel) =>
      document.querySelector(sel)?.getClientRects().length ?? 0;
    return {
      appearance: rects("#settings-appearance") > 0,
      model: rects("#settings-model") > 0,
      data: rects("#settings-data") > 0,
      subagents: rects("#settings-subagents") > 0,
      rollback: rects("#subagent-rollback") > 0,
      kindRows: document.querySelectorAll("#subagent-kind-rows > *").length,
      advancedExpanded: document
        .querySelector("#settings-advanced-toggle")
        ?.getAttribute("aria-expanded"),
      sidebarVersion: Boolean(document.querySelector(".version-line")),
    };
  });
  expect(before.appearance).toBe(true);
  expect(before.model).toBe(true);
  expect(before.data).toBe(true);
  expect(before.subagents).toBe(false);
  expect(before.rollback).toBe(false);
  expect(before.kindRows).toBe(0);
  expect(before.advancedExpanded).toBe("false");
  expect(before.sidebarVersion).toBe(false);

  await page.getByRole("button", { name: "高级", exact: true }).click();
  await expect(page.locator("#settings-subagents")).toBeVisible();
  await expect(page.locator("#subagent-kind-rows > *")).toHaveCount(4);
  await expect(
    page.getByRole("button", { name: "全部回退到影子" }),
  ).toBeVisible();
  const after = await page.evaluate(() => ({
    advancedExpanded: document
      .querySelector("#settings-advanced-toggle")
      ?.getAttribute("aria-expanded"),
    versionInsideAdvanced: Boolean(
      document.querySelector("#settings-subagents .settings-hint"),
    ),
  }));
  expect(after.advancedExpanded).toBe("true");
  expect(after.versionInsideAdvanced).toBe(true);
});
