import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import {
  installExecutionFocusFixture,
  openFocusWorkspace,
} from "../fixtures/execution-focus.mjs";

async function idle(page) {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.status = "completed";
  fixture.first.error = "已保存的执行诊断";
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  return fixture;
}

test("an idle workspace recovers its status automatically after a transient read failure", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.status = "completed";
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  let failures = 0;
  await page.route("**/api/execution/focus-a?**", async (route) => {
    if (!failures++)
      await route.fulfill({
        status: 503,
        json: { error: { message: "temporary status read failure" } },
      });
    else await route.fallback();
  });
  await expect
    .poll(() => failures, { timeout: 8000 })
    .toBeGreaterThanOrEqual(2);
  await expect(page.locator("#execution-connection")).toBeHidden();
});

test("manual reconnect preserves file, task draft and execution errors, rejects duplicate reads and restores keyboard focus", async ({
  page,
}, info) => {
  const fixture = await idle(page);
  await page
    .locator('#execution-quick-files button[data-path="results.csv"]')
    .click();
  await page.getByLabel("描述执行任务").fill("保留这份还未发送的任务");
  let reads = 0,
    held;
  await page.route("**/api/execution/focus-a?**", async (route) => {
    reads++;
    if (reads === 1)
      await route.fulfill({
        status: 503,
        json: {
          error: {
            message: '<img src=x onerror="window.RECONNECT_INJECTED=1">',
          },
        },
      });
    else held = route;
  });
  const panel = page.locator("#execution-connection");
  await expect(panel).toBeVisible();
  await expect(panel.locator("details")).not.toHaveAttribute("open", "");
  await panel.locator("summary").click();
  await expect(page.locator("#execution-connection-error")).toContainText(
    "<img",
  );
  expect(await page.evaluate(() => window.RECONNECT_INJECTED)).toBeUndefined();
  await expect(page.locator("#execution-preview table")).toContainText("1250");
  await expect(page.getByLabel("描述执行任务")).toHaveValue(
    "保留这份还未发送的任务",
  );
  await expect(page.locator("#execution-feedback")).toHaveText(
    "已保存的执行诊断",
  );
  await panel.screenshot({ path: info.outputPath("connection-details.png") });
  const retry = page.locator("#execution-connection-retry");
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(retry).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Enter");
  await expect.poll(() => reads).toBe(2);
  await held.fulfill({ json: fixture.first });
  await expect(panel).toBeHidden();
  await expect(page.locator("#execution-focus-label")).toBeFocused();
  await expect(page.getByLabel("描述执行任务")).toHaveValue(
    "保留这份还未发送的任务",
  );
  await expect(page.locator("#execution-feedback")).toHaveText(
    "已保存的执行诊断",
  );
});

test("a stalled request times out, permits reconnection and keeps the user's active input focus", async ({
  page,
}) => {
  const fixture = await idle(page);
  let held,
    reads = 0;
  await page.route("**/api/execution/focus-a?**", async (route) => {
    if (!reads++) held = route;
    else await route.fallback();
  });
  const input = page.getByLabel("描述执行任务");
  await input.fill("超时期间仍在编辑的任务");
  await expect(page.locator("#execution-connection")).toBeVisible({
    timeout: 20000,
  });
  await expect(page.locator("#execution-connection-error")).toContainText(
    "超时",
  );
  await expect(page.locator("#execution-connection-retry")).toHaveAttribute(
    "aria-disabled",
    "false",
  );
  await expect(page.locator("#execution-connection")).toBeHidden({
    timeout: 5000,
  });
  await expect(input).toBeFocused();
  await expect(input).toHaveValue("超时期间仍在编辑的任务");
  expect(fixture.answers).toEqual([]);
  await held.abort().catch(() => {});
});

test("late older poll responses cannot revert a newer submitted answer", async ({
  page,
}) => {
  const fixture = await installExecutionFocusFixture(page);
  await page.goto("/");
  await openFocusWorkspace(page);
  fixture.first.clarifications = [
    {
      id: "recovery-q",
      turn_id: "turn-1",
      status: "pending",
      question: "选择展示方式",
      options: ["图表", "数据表"],
    },
  ];
  await expect(
    page.getByRole("button", { name: "图表", exact: true }),
  ).toBeVisible();
  let held, old;
  await page.route("**/api/execution/focus-a?**", async (route) => {
    if (!held) {
      held = route;
      old = JSON.parse(JSON.stringify(fixture.first));
    } else await route.fallback();
  });
  await expect.poll(() => Boolean(held)).toBe(true);
  await page.getByRole("button", { name: "图表", exact: true }).click();
  await page.getByRole("button", { name: "发送回答 ↗", exact: true }).click();
  await expect(page.locator(".execution-question-history")).toContainText(
    "已核对",
  );
  await page.evaluate(() => {
    window.answerRegressions = [];
    new MutationObserver(() => {
      if (
        document
          .querySelector("#execution-clarifications")
          .textContent.includes("需要你核对")
      )
        window.answerRegressions.push(true);
    }).observe(document.querySelector("#execution-clarifications"), {
      subtree: true,
      childList: true,
      characterData: true,
    });
  });
  const arrived = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/execution/focus-a",
  );
  await held.fulfill({ json: old });
  await arrived;
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  expect(await page.evaluate(() => window.answerRegressions)).toEqual([]);
  await expect(page.locator(".execution-question-history")).toContainText(
    "已核对",
  );
  expect(fixture.answers).toHaveLength(1);
});

test("invalid status data and delayed requests cannot replace the selected workspace", async ({
  page,
}) => {
  await idle(page);
  let reads = 0,
    held;
  await page.route("**/api/execution/focus-a?**", async (route) => {
    if (!reads++) await route.fulfill({ json: {} });
    else held = route;
  });
  await expect(page.locator("#execution-connection")).toBeVisible();
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  await expect(page.locator("#execution-connection-error")).toContainText(
    "无效",
  );
  await page.locator("#execution-connection-retry").click();
  await expect.poll(() => Boolean(held)).toBe(true);
  await page.locator("#execution-workspace").selectOption("focus-b");
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "已保存的工作区",
  );
  await expect(page.locator("#execution-connection")).toBeHidden();
  await held
    .fulfill({
      json: { id: "focus-a", status: "running", sequence: 999, events: [] },
    })
    .catch(() => {});
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "已保存的工作区",
  );
});

test("failed reads back off and do not continue while another view is open", async ({
  page,
}) => {
  await idle(page);
  const times = [];
  await page.route("**/api/execution/focus-a?**", async (route) => {
    times.push(Date.now());
    await route.fulfill({
      status: 503,
      json: { error: { message: "暂时离线" } },
    });
  });
  await expect
    .poll(() => times.length, { timeout: 9000 })
    .toBeGreaterThanOrEqual(3);
  expect(times[1] - times[0]).toBeGreaterThanOrEqual(900);
  expect(times[2] - times[1]).toBeGreaterThanOrEqual(1900);
  await page.getByRole("button", { name: "问诊", exact: true }).click();
  const stopped = times.length;
  await page.waitForTimeout(4500);
  expect(times).toHaveLength(stopped);
  const resumed = page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/api/execution/focus-a",
  );
  await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  await resumed;
  expect(times.length).toBeGreaterThan(stopped);
});

for (const width of [320, 390]) {
  test(`recovery and preview actions have comfortable touch targets and contrast at ${width}px in six themes`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 844 });
    await idle(page);
    await page
      .locator('#execution-quick-files button[data-path="results.csv"]')
      .click();
    await page.route("**/api/execution/focus-a?**", (route) =>
      route.fulfill({
        status: 503,
        json: { error: { message: "临时连接问题" } },
      }),
    );
    await expect(page.locator("#execution-connection")).toBeVisible();
    const axe = await readFile(
      new URL("../../node_modules/axe-core/axe.min.js", import.meta.url),
      "utf8",
    );
    await page.route("**/recovery-audit.js", (route) =>
      route.fulfill({ contentType: "application/javascript", body: axe }),
    );
    await page.addScriptTag({ url: "/recovery-audit.js" });
    for (const theme of ["forest", "ocean", "iris", "sand", "slate", "night"]) {
      await page.evaluate(
        (value) => (document.documentElement.dataset.theme = value),
        theme,
      );
      const actions = await page
        .locator(
          "#execution-connection button, #execution-preview-controls button",
        )
        .evaluateAll((nodes) =>
          nodes
            .filter((node) => node.getBoundingClientRect().width)
            .map((node) => {
              const box = node.getBoundingClientRect();
              return {
                text: node.textContent.trim(),
                width: box.width,
                height: box.height,
                left: box.left,
                right: box.right,
              };
            }),
        );
      expect(
        actions.every(
          (box) =>
            box.width >= 44 &&
            box.height >= 44 &&
            box.left >= 0 &&
            box.right <= width,
        ),
        JSON.stringify(actions),
      ).toBe(true);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      const violations = await page.evaluate(async () => {
        const report = await window.axe.run(
          {
            include: [
              ["#execution-connection"],
              ["#execution-preview-controls"],
            ],
          },
          {
            runOnly: {
              type: "rule",
              values: [
                "color-contrast",
                "button-name",
                "label",
                "aria-allowed-attr",
                "aria-valid-attr-value",
              ],
            },
          },
        );
        return report.violations.map((item) => ({
          id: item.id,
          nodes: item.nodes.map((node) => node.target),
        }));
      });
      expect(violations).toEqual([]);
      await page
        .locator("#execution-connection")
        .screenshot({ path: info.outputPath(`recovery-${theme}.png`) });
    }
    await page.screenshot({
      path: info.outputPath("workspace-recovery.png"),
      fullPage: true,
    });
  });
}
