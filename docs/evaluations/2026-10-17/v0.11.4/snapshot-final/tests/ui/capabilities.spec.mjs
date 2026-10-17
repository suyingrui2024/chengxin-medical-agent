import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const shots = "docs/assets/ui";
test.beforeAll(async () => {
  await mkdir(shots, { recursive: true });
});

test("patient view removes developer navigation while keeping the same conversation", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "模型与运行配置" }),
  ).toBeHidden();
  await expect(page.locator('.nav-item[data-view="inspect"]')).toBeHidden();
  await expect(
    page.locator('.nav-item[data-view="capabilities"]'),
  ).toBeHidden();
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByLabel("界面模式").selectOption("developer");
  await expect(
    page.locator('.nav-item[data-view="capabilities"]'),
  ).toBeVisible();
  await page.locator('.nav-item[data-view="inspect"]').click();
  await page.getByLabel("界面模式").selectOption("patient");
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
    await page.getByLabel("界面模式").selectOption("developer");
    await page.locator('.nav-item[data-view="capabilities"]').click();
    await expect(
      page.getByRole("heading", { name: "让能力可组合，也可核对。" }),
    ).toBeVisible();
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
    await expect(page.getByText("0 / 3 已启用", { exact: true })).toBeVisible();
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
