import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { verifyDirectoryRecovery } from "../fixtures/directory-recovery.mjs";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

test("a stalled directory read times out and retries without losing the file list or task draft", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.status = "completed";
  await page.locator("#execution-file-browser > summary").click();
  const input = page.getByLabel("描述执行任务");
  await input.fill("保留这份还没发送的任务");
  let reads = 0;
  const routes = [];
  await page.route("**/api/execution/focus-a/directory?**", (route) => {
    reads++;
    routes.push(route);
  });
  await expect.poll(() => reads, { timeout: 21000 }).toBeGreaterThanOrEqual(2);
  await expect(page.locator("#execution-directory-status")).toContainText(
    "上次列表",
  );
  await expect(page.locator("#execution-files button")).toHaveCount(4);
  await expect(page.locator("#execution-files button").first()).toBeDisabled();
  await expect(input).toHaveValue("保留这份还没发送的任务");
  await input.focus();
  await routes.at(-1).fallback();
  await expect(page.locator("#execution-directory-retry")).toBeHidden();
  await expect(page.locator("#execution-files button").first()).toBeEnabled();
  await expect(input).toBeFocused();
  expect(fixture.answers).toEqual([]);
});

test("manual directory recovery preserves preview, draft and keyboard focus without duplicate reads", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  await page
    .getByRole("button", { name: "快速查看：results.csv", exact: true })
    .click();
  fixture.first.status = "completed";
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  await verifyDirectoryRecovery(page);
});

test("failed navigation retries the requested folder and does not relabel the previous listing", async ({
  page,
}) => {
  await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  await page.locator("#execution-file-browser > summary").click();
  await page.locator(".directory-direct > summary").click();
  let reads = 0;
  const folder = {
    schema_version: "execution-directory/v1",
    path: "reports",
    include_hidden: false,
    entries: [
      {
        name: "final.csv",
        path: "reports/final.csv",
        kind: "file",
        size: 20,
        revision: "folder",
      },
    ],
    scan_complete: true,
    total_entries: 1,
    reasons: [],
    next_cursor: null,
  };
  await page.route("**/api/execution/focus-a/directory?**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("path") !== "reports") return route.fallback();
    if (!reads++)
      await route.fulfill({
        status: 503,
        json: { error: { message: "folder temporarily unavailable" } },
      });
    else await route.fulfill({ json: folder });
  });
  await page.getByLabel("文件相对路径").fill("reports/final.csv");
  await page.locator("#execution-file-open-button").click();
  await expect(page.locator("#execution-directory-retry")).toBeVisible();
  await expect(page.locator("#execution-files button")).toHaveCount(0);
  await expect.poll(() => reads, { timeout: 7000 }).toBeGreaterThanOrEqual(2);
  await expect(page.locator("#execution-files button")).toHaveCount(1);
  await expect(page.locator("#execution-files")).toContainText("final.csv");
  await expect(
    page.locator("#execution-directory-breadcrumb [aria-current]"),
  ).toHaveText("reports");
});

test("switching workspaces aborts an outstanding directory read and clears the old scope", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.status = "completed";
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  let reads = 0;
  const pattern = "**/api/execution/focus-a/directory?**";
  await page.route(pattern, () => {
    reads++;
  });
  await expect.poll(() => reads).toBe(1);
  const aborted = page.waitForEvent("requestfailed", (request) =>
    request.url().includes("/focus-a/directory?"),
  );
  await page.locator("#execution-workspace").selectOption("focus-b");
  await aborted;
  await expect(page.locator("#execution-directory-status")).toContainText(
    "0 项",
  );
  await expect(page.locator("#execution-files button")).toHaveCount(0);
  await expect(page.locator("#execution-directory-retry")).toBeHidden();
});

test("changing the hidden-file scope cancels the prior read and cannot retain hidden entries after a failed refresh", async ({
  page,
}) => {
  await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  await page.locator("#execution-file-browser > summary").click();
  let reads = 0,
    rejectVisible = false;
  await page.route("**/api/execution/focus-a/directory?**", async (route) => {
    reads++;
    if (reads === 1) return;
    const hidden =
      new URL(route.request().url()).searchParams.get("hidden") === "1";
    if (!hidden && rejectVisible)
      return route.fulfill({
        status: 503,
        json: { error: { message: "temporarily unavailable" } },
      });
    await route.fulfill({
      json: {
        schema_version: "execution-directory/v1",
        path: "",
        include_hidden: hidden,
        entries: hidden
          ? [
              {
                path: ".private.txt",
                name: ".private.txt",
                kind: "file",
                size: 4,
                revision: "hidden",
              },
            ]
          : [],
        total_entries: hidden ? 1 : 0,
        scan_complete: true,
        reasons: [],
        next_cursor: null,
      },
    });
  });
  await expect.poll(() => reads).toBe(1);
  const aborted = page.waitForEvent("requestfailed", (request) =>
    request.url().includes("/focus-a/directory?"),
  );
  await page.locator("#execution-directory-hidden").check();
  await aborted;
  await expect(page.locator("#execution-files")).toContainText(".private.txt");
  rejectVisible = true;
  await page.locator("#execution-directory-hidden").uncheck();
  await expect(page.locator("#execution-directory-retry")).toBeVisible();
  await expect(page.locator("#execution-files")).not.toContainText(
    ".private.txt",
  );
  rejectVisible = false;
  await page.locator("#execution-directory-retry").click();
  await expect(page.locator("#execution-directory-status")).toContainText(
    "0 项",
  );
  await expect(page.locator("#execution-directory-retry")).toBeHidden();
});

for (const width of [320, 390]) {
  test(`directory recovery and touch controls fit ${width}px in six themes`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 844 });
    await installExecutionFocusFixture(page);
    await page.goto("/");
    await openFocusWorkspace(page);
    await page.locator("#execution-file-browser > summary").click();
    await page.route("**/api/execution/focus-a/directory?**", (route) =>
      route.fulfill({
        status: 503,
        json: { error: { message: "temporary directory failure" } },
      }),
    );
    await expect(page.locator("#execution-directory-retry")).toBeVisible();
    await page.evaluate(
      await readFile("node_modules/axe-core/axe.min.js", "utf8"),
    );
    for (const theme of ["forest", "ocean", "iris", "sand", "slate", "night"]) {
      await page.evaluate(
        (value) => (document.documentElement.dataset.theme = value),
        theme,
      );
      const boxes = await page
        .locator(
          ".directory-state, .directory-state button, .directory-toolbar label, .directory-direct > summary, #execution-files button",
        )
        .evaluateAll((nodes) =>
          nodes.map((node) => {
            const b = node.getBoundingClientRect();
            return {
              left: b.left,
              right: b.right,
              width: b.width,
              height: b.height,
            };
          }),
        );
      expect(
        boxes.every(
          (b) =>
            b.left >= 0 && b.right <= width && b.width >= 44 && b.height >= 44,
        ),
      ).toBe(true);
      const issues = await page.evaluate(
        async () =>
          (
            await window.axe.run("#execution-file-browser", {
              runOnly: {
                type: "rule",
                values: [
                  "color-contrast",
                  "button-name",
                  "label",
                  "target-size",
                ],
              },
            })
          ).violations,
      );
      expect(issues).toEqual([]);
      await page.locator("#execution-file-browser").screenshot({
        path: info.outputPath(`directory-${width}-${theme}.png`),
      });
    }
  });
}
