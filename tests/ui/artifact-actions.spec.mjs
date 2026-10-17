import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

async function start(page) {
  const fixture = await installExecutionFocusFixture(page);
  await page.addInitScript(() => {
    window.copied = [];
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async (text) => {
          if (window.rejectCopy) throw new Error("剪贴板被拒绝");
          window.copied.push(text);
        },
      },
    });
  });
  await page.goto("/");
  await openFocusWorkspace(page);
  return fixture;
}
test("copy and save preserve exact inert original text, independent of table or chart mode", async ({
  page,
}) => {
  const fixture = await start(page);
  await page
    .getByRole("button", { name: "快速查看：results.csv", exact: true })
    .click();
  await page.locator("#execution-preview-chart").click();
  await page.locator("#execution-preview-copy").click();
  expect(await page.evaluate(() => window.copied)).toEqual([
    "sample,mass\nA,1250",
  ]);
  const downloaded = page.waitForEvent("download");
  await page.locator("#execution-preview-save").click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("results.csv.snapshot.txt");
  expect(await readFile(await download.path(), "utf8")).toBe(
    "sample,mass\nA,1250",
  );
  await page
    .getByRole("button", { name: "快速查看：notes<draft>.md", exact: true })
    .click();
  await page.evaluate(() => (window.rejectCopy = true));
  await page.locator("#execution-preview-copy").click();
  await expect(page.locator("#execution-preview-action-status")).toHaveText(
    "未能复制，请重试、保存快照或手动复制。",
  );
  await expect(page.locator("#execution-preview-copy")).toBeEnabled();
  await page.evaluate(() => (window.rejectCopy = false));
  await page.locator("#execution-preview-copy").click();
  expect((await page.evaluate(() => window.copied)).at(-1)).toContain(
    "<img src=x onerror=",
  );
  expect(await page.evaluate(() => window.FOCUS_INJECTED)).toBeUndefined();
  expect(fixture.answers).toEqual([]);
  fixture.first.status = "completed";
  await expect(page.locator("#execution-workspace")).toBeEnabled();
  await page.locator("#execution-workspace").selectOption("focus-b");
  await expect(page.locator("#execution-preview-controls")).toBeHidden();
  await expect(page.locator("#execution-preview-action-status")).toBeEmpty();
});

test("image exports verify actual bytes and reject stale image content", async ({
  page,
}) => {
  await start(page);
  const image = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
    "base64",
  );
  const sha256 = createHash("sha256").update(image).digest("hex");
  await page.route("**/api/execution/focus-a/preview?**", (route) =>
    route.fulfill({
      json: {
        schema_version: "execution-artifact/v1",
        kind: "image",
        path: "chart.png",
        revision: "fixture-1",
        sha256,
        size: image.length,
        mime: "image/png",
        width: 1,
        height: 1,
      },
    }),
  );
  await page
    .getByRole("button", { name: "快速查看：chart.png", exact: true })
    .click();
  await expect(page.locator("#execution-preview-copy")).toBeHidden();
  const downloaded = page.waitForEvent("download");
  await page.locator("#execution-preview-save").click();
  const download = await downloaded;
  expect(await readFile(await download.path())).toEqual(image);
  expect(download.suggestedFilename()).toBe("chart.png.snapshot.png");
  await page.route("**/api/execution/focus-a/image?**", (route) =>
    route.fulfill({ body: Buffer.from("changed") }),
  );
  let downloads = 0;
  page.on("download", () => downloads++);
  await page.locator("#execution-preview-save").click();
  await expect(page.locator("#execution-preview-action-status")).toContainText(
    "不一致",
  );
  expect(downloads).toBe(0);
  await expect(page.locator("#execution-preview-save")).toBeEnabled();
});

test("a delayed image save cannot export into a newly selected workspace", async ({
  page,
}) => {
  const fixture = await start(page);
  fixture.first.status = "completed";
  await expect(page.locator("#execution-workspace")).toBeEnabled();
  await page
    .getByRole("button", { name: "快速查看：chart.png", exact: true })
    .click();
  let release;
  const held = new Promise((resolve) => (release = resolve));
  let downloads = 0;
  page.on("download", () => downloads++);
  await page.route("**/api/execution/focus-a/image?**", async (route) => {
    await held;
    await route.fulfill({ status: 409, body: "changed" });
  });
  const requested = page.waitForRequest((request) =>
    request.url().includes("/image?"),
  );
  await page.locator("#execution-preview-save").click();
  await requested;
  await page.locator("#execution-workspace").selectOption("focus-b");
  const responded = page.waitForResponse((response) =>
    response.url().includes("/image?"),
  );
  release();
  await responded;
  await expect(page.locator("#execution-preview-action-status")).toBeEmpty();
  await expect(page.locator("#execution-preview-controls")).toBeHidden();
  expect(downloads).toBe(0);
});
