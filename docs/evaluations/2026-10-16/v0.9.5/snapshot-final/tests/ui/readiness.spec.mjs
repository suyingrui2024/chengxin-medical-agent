import { test, expect } from "@playwright/test";

test("workspace mode cannot lose an interaction before module hydration", async ({
  page,
}) => {
  let ready;
  const gate = new Promise((resolve) => {
    ready = resolve;
  });
  await page.route("**/app.js", async (route) => {
    await gate;
    await route.continue();
  });
  try {
    await page.goto("/", { waitUntil: "commit" });
    const mode = page.getByLabel("界面模式");
    await expect(mode).toBeDisabled();
    ready();
    await expect(mode).toBeEnabled();
    await mode.selectOption("developer");
    await expect(page.locator("body")).toHaveAttribute(
      "data-mode",
      "developer",
    );
    await page.getByRole("button", { name: "执行工作台", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "把任务，真正做出来。" }),
    ).toBeVisible();
    await expect(page.getByText("本机服务已连接")).toBeVisible();
    await expect(page.locator("body")).toHaveAttribute(
      "data-mode",
      "developer",
    );
  } finally {
    ready();
  }
});
