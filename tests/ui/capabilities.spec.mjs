import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const shots = "docs/assets/ui";
test.beforeAll(async () => {
  await mkdir(shots, { recursive: true });
});

/**
 * 「界面模式」改版后移入设置视图，不再是常驻控件。
 * 必须先点设置导航，否则选择器根本不在 DOM 的可交互区域。
 */
async function setInterfaceMode(page, mode) {
  await page.locator('.nav-item[data-view="settings"]').click();
  await page.getByLabel("界面模式").selectOption(mode);
}

test("patient view keeps model service while removing developer navigation", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  // 模型服务在患者视图仍可达——入口是设置导航，不再是独立按钮。
  await expect(page.locator('.nav-item[data-view="settings"]')).toBeVisible();
  for (const view of ["consultation", "history", "records"]) {
    await expect(page.locator(`.nav-item[data-view="${view}"]`)).toBeVisible();
  }
  await expect(page.locator('.nav-item[data-view="inspect"]')).toBeHidden();
  await expect(
    page.locator('.nav-item[data-view="capabilities"]'),
  ).toBeHidden();
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await setInterfaceMode(page, "developer");
  await expect(page.locator("#workspace-mode-description")).toHaveText(
    "显示患者流程和全部研发测试功能",
  );
  for (const view of [
    "consultation",
    "history",
    "records",
    "tasks",
    "inspect",
    "capabilities",
    "execution",
  ]) {
    await expect(page.locator(`.nav-item[data-view="${view}"]`)).toBeVisible();
  }
  await expect(
    page.locator('.nav-item[data-view="capabilities"]'),
  ).toBeVisible();
  await page.locator('.nav-item[data-view="inspect"]').click();
  await setInterfaceMode(page, "patient");
  await expect(page.locator("#workspace-mode-description")).toHaveText(
    "只显示患者使用的功能",
  );
  // 切换模式后停在设置页；回到问诊页才能看到输入框。
  await page.locator('.nav-item[data-view="consultation"]').click();
  await expect(
    page.getByRole("textbox", { name: "输入你的描述" }),
  ).toBeVisible();
  await expect(page.locator(".message.user")).toContainText("我自己");
});

test("capability workspace calculates without writing cases and pins old session versions", async ({
  page,
  request,
}) => {
  try {
    await page.goto("/");
    await expect(page.getByText("本机服务已连接")).toBeVisible();
    const originalResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/sessions"),
    );
    await page.getByRole("button", { name: "开始问诊", exact: true }).click();
    const original = await (await originalResponse).json();
    await setInterfaceMode(page, "developer");
    await page.locator('.nav-item[data-view="capabilities"]').click();
    await expect(
      page.getByRole("heading", { name: "让能力可组合，也可核对。" }),
    ).toBeVisible();
    await page.locator("#capability-select").selectOption("calculate.units");
    await page.getByRole("button", { name: "执行并查看公式" }).click();
    await expect(page.locator("#capability-test-result")).toContainText(
      "1250 g",
    );
    await expect(page.locator("#capability-test-result")).toContainText(
      "1.25 × 1000 ÷ 1 = 1250 g",
    );
    const unchanged = await (
      await request.get(`/api/sessions/${original.session_id}`)
    ).json();
    expect(unchanged.state.facts).toEqual(original.state.facts);
    expect(unchanged.state.calculations).toEqual([]);
    await page.screenshot({
      path: `${shots}/08-capabilities.png`,
      fullPage: true,
    });
    await page.getByLabel("目标单位").selectOption("mL");
    await page.getByRole("button", { name: "执行并查看公式" }).click();
    await expect(page.locator("#capability-test-feedback")).toContainText(
      "不能直接换算",
    );
    await expect(page.locator("#capability-test-result")).toBeEmpty();
    await page.locator("#capability-select").selectOption("calculate.glucose");
    await expect(
      page.getByLabel("原单位", { exact: true }).locator("option"),
    ).toHaveCount(2);
    await page.getByRole("button", { name: "执行并查看公式" }).click();
    await expect(
      page.locator("#capability-test-result .calculation-card h3"),
    ).toHaveText("≈ 180 mg/dL");
    await page
      .getByRole("button", { name: "停用 测一次腋下体温", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "启用 测一次腋下体温", exact: true }),
    ).toBeVisible();
    await expect(page.locator("#composition-note")).toContainText(
      "与新配置不同",
    );
    const old = await (
      await request.get(`/api/sessions/${original.session_id}`)
    ).json();
    expect(old.state.runtime_snapshot).toEqual(original.state.runtime_snapshot);
    const newResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/sessions"),
    );
    await page.locator("#new-session").click();
    const newer = await (await newResponse).json();
    expect(
      newer.state.runtime_snapshot.composition.capabilities.find(
        (item) => item.id === "observe.temperature",
      ).enabled,
    ).toBe(false);
    await page.locator('.nav-item[data-view="capabilities"]').click();
    await page.getByLabel("新问诊模板").selectOption("history");
    await page.getByRole("button", { name: "应用模板", exact: true }).click();
    // 断言计数器渲染，不绑定具体数字：启用数量随模板变化，
    // 硬编码会让测试在每次调整模板时误报。
    await expect(page.locator("#capability-count")).toContainText("已启用");
    await expect(
      page.getByRole("button", { name: "执行并查看公式" }),
    ).toBeDisabled();
  } finally {
    const catalog = await (await request.get("/api/capabilities")).json();
    const response = await request.post("/api/capabilities/configure", {
      data: { template_id: "general", expected_revision: catalog.revision },
    });
    expect(response.ok()).toBe(true);
  }
});

test("a catalog loading in the background cannot replace a template selection", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  await setInterfaceMode(page, "developer");
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.route("**/api/capabilities", async (route) => {
    await gate;
    await route.continue();
  });
  try {
    await page.locator('.nav-item[data-view="capabilities"]').click();
    await expect(page.getByLabel("新问诊模板")).toBeDisabled();
    release();
    await expect(page.getByLabel("新问诊模板")).toBeEnabled();
    await page.getByLabel("新问诊模板").selectOption("history");
    await page.getByRole("button", { name: "应用模板", exact: true }).click();
    // 断言计数器渲染，不绑定具体数字：启用数量随模板变化，
    // 硬编码会让测试在每次调整模板时误报。
    await expect(page.locator("#capability-count")).toContainText("已启用");
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
    const catalog = await (await page.request.get("/api/capabilities")).json();
    await page.request.post("/api/capabilities/configure", {
      data: { template_id: "general", expected_revision: catalog.revision },
    });
  }
});
