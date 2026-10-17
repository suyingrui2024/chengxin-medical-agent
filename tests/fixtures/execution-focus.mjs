// Synthetic host snapshots for rendering and interaction checks; no model calls.
import { expect } from "@playwright/test";
const pixel = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
  "base64",
);
export async function installExecutionFocusFixture(page) {
  const first = {
    id: "focus-a",
    title: "整理三组换算结果，生成图表与数据表",
    directory: "/synthetic/workspace",
    status: "running",
    execution_configured: true,
    model: "local-rendering-fixture",
    sequence: 0,
    turn_id: "turn-1",
    turns: [{ id: "turn-1", model_calls: 2, time_budget_seconds: 180 }],
    events: [],
    jobs: [],
    calculations: [],
    clarifications: [],
    artifacts: [],
    artifact_scan: { performed: false },
  };
  const second = {
    ...first,
    id: "focus-b",
    title: "另一份已保存的工作区",
    status: "saved",
    turns: [],
    turn_id: null,
    restored: true,
    execution_configured: false,
  };
  const states = new Map([
    [first.id, first],
    [second.id, second],
  ]);
  let previews = 0;
  await page.route("**/api/health", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), execution_available: true },
    });
  });
  await page.route("**/api/execution**", async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length > 2 && !states.has(parts[2])) {
      await route.fallback();
      return;
    }
    const state = states.get(parts[2]) || first;
    const operation = parts[3];
    let data;
    if (parts.length === 2) data = { sessions: [first, second] };
    else if (operation === "directory") {
      const paths =
        state.id === first.id
          ? ["chart.png", "results.csv", "notes<draft>.md", "script.py"]
          : [];
      data = {
        schema_version: "execution-directory/v1",
        path: "",
        include_hidden: false,
        entries: paths.map((path) => ({
          path,
          name: path,
          kind: "file",
          size: 68,
          revision: "fixture-1",
        })),
        scan_complete: true,
        reasons: [],
        total_entries: paths.length,
        next_cursor: null,
      };
    } else if (operation === "preview") {
      previews++;
      const path = url.searchParams.get("path");
      data = {
        schema_version: "execution-artifact/v1",
        path,
        revision: "fixture-1",
        sha256: "a".repeat(64),
        size: 68,
        ...(path.endsWith(".png")
          ? { kind: "image", mime: "image/png", width: 1, height: 1 }
          : path.endsWith(".csv")
            ? {
                kind: "table",
                text: "sample,mass\nA,1250",
                rows: [
                  ["sample", "mass"],
                  ["A", "1250"],
                ],
                columns: 2,
                total_rows: 2,
              }
            : {
                kind: "text",
                text: '<img src=x onerror="window.FOCUS_INJECTED=1">',
              }),
      };
    } else if (operation === "image") {
      await route.fulfill({ contentType: "image/png", body: pixel });
      return;
    } else if (operation === "artifact-status")
      data = {
        schema_version: "artifact-freshness/v1",
        path: url.searchParams.get("path"),
        matches: true,
      };
    else if (operation === "changes")
      data = {
        schema: "workspace-changes/v1",
        state: "baseline_unavailable",
        turn_id: state.turn_id,
        counts: null,
        entries: [],
      };
    else if (operation === "answer") {
      state.clarifications = state.clarifications.map((q) => ({
        ...q,
        status: "answered",
        answer: "生成图表",
      }));
      state.sequence++;
      data = state;
    } else data = state;
    await route.fulfill({ json: data });
  });
  return { first, second, previews: () => previews };
}
export async function openFocusWorkspace(page) {
  if (await page.locator("#mobile-menu").isVisible())
    await page.locator("#mobile-menu").click();
  await page.getByLabel("界面模式").selectOption("developer");
  if (await page.locator("#mobile-menu").isVisible())
    await page.locator("#mobile-menu").click();
  await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  await page.locator("#execution-refresh").click();
  await expect(
    page.locator('#execution-workspace option[value="focus-a"]'),
  ).toHaveCount(1);
  await page.locator("#execution-workspace").selectOption("focus-a");
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "正在处理任务",
  );
  await expect(page.locator("#execution-quick-files button")).toHaveCount(3);
  await page.locator("#execution-quick-look > summary").click();
}
