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
  const answers = [];
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
      const answer = route.request().postDataJSON();
      answers.push(answer);
      state.clarifications = state.clarifications.map((q) => ({
        ...q,
        ...(q.id === answer.request_id
          ? { status: "answered", answer: answer.answer }
          : {}),
      }));
      state.sequence++;
      data = state;
    } else data = state;
    await route.fulfill({ json: data });
  });
  return { first, second, previews: () => previews, answers };
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

export async function verifyEditableAnswer(page, fixture) {
  fixture.first.clarifications = [
    {
      id: "native-draft",
      turn_id: "turn-1",
      status: "pending",
      question: "希望如何查看结果？",
      options: ["图表", "数据表"],
    },
  ];
  const card = page.locator(
    ".execution-question:not(.execution-question-history)",
  );
  await card.getByRole("button", { name: "图表", exact: true }).click();
  await card.getByRole("button", { name: "数据表", exact: true }).click();
  expect(fixture.answers).toEqual([]);
  await card.getByRole("button", { name: "自行说明", exact: true }).click();
  await expect(card.getByLabel("补充回答")).toHaveValue("数据表");
  await card.getByLabel("补充回答").fill("数据表，保留原始顺序");
  await card.getByRole("button", { name: "发送回答 ↗", exact: true }).click();
  await expect.poll(() => fixture.answers.length).toBe(1);
  expect(fixture.answers[0]).toEqual({
    request_id: "native-draft",
    turn_id: "turn-1",
    answer: "数据表，保留原始顺序",
  });
  return { passed: true, host_snapshot_fixture: true, edited_answer: true };
}

export async function verifyExecutionRecovery(page, fixture) {
  fixture.first.status = "completed";
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  const input = page.getByLabel("描述执行任务");
  await input.fill("保留这份尚未发送的接续任务");
  let reads = 0;
  const pattern = "**/api/execution/focus-a?**";
  const failure = async (route) => {
    if (!reads++)
      await route.fulfill({
        status: 503,
        json: { error: { message: "临时读取失败：恢复检查" } },
      });
    else await route.fallback();
  };
  await page.route(pattern, failure);
  try {
    await expect(page.locator("#execution-connection")).toBeVisible();
    await page.locator("#execution-connection-retry").click();
    await expect(page.locator("#execution-connection")).toBeHidden();
    await expect(input).toHaveValue("保留这份尚未发送的接续任务");
    await expect(page.locator("#execution-preview table")).toContainText(
      "1250",
    );
    return {
      passed: true,
      host_snapshot_fixture: true,
      read_failure_and_manual_retry: true,
      draft_preserved: true,
    };
  } finally {
    await page.unroute(pattern, failure);
  }
}

export async function verifyEarlierChildAnswer(page, fixture) {
  fixture.first.turns.unshift({ id: "prior-turn", model_calls: 3 });
  fixture.first.clarifications.push({
    id: "native-earlier-child",
    session_id: "prior-child",
    turn_id: "prior-turn",
    status: "pending",
    question: "原任务保留哪种格式？",
    options: ["CSV", "JSON"],
  });
  const card = page.locator('[data-question-id="native-earlier-child"]');
  await expect(card.locator(".execution-question-heading")).toContainText(
    "较早子任务",
  );
  await card.getByRole("button", { name: "CSV", exact: true }).click();
  await card.getByRole("button", { name: "发送回答 ↗", exact: true }).click();
  await expect
    .poll(() => fixture.answers.at(-1)?.request_id)
    .toBe("native-earlier-child");
  expect(fixture.answers.at(-1)).toEqual({
    request_id: "native-earlier-child",
    turn_id: "prior-turn",
    answer: "CSV",
  });
  expect(fixture.first.turn_id).toBe("turn-1");
  await expect(card).toHaveCount(0);
  return {
    passed: true,
    host_snapshot_fixture: true,
    earlier_turn_preserved: true,
    external_model_calls: 0,
  };
}

export async function verifyIncompleteExplanation(page, fixture) {
  const state = fixture.first;
  const text = "已保存实现；交付检查仍未通过。请核对现有文件后继续。";
  state.status = "failed";
  state.error = "delivery_checks_failed";
  const sequence = ++state.sequence;
  state.events.push({
    sequence,
    type: "turn.failed",
    turn_id: state.turn_id,
    error_type: state.error,
    final_response: text,
  });
  await expect(page.locator("#execution-focus-label")).toHaveText(
    "本轮尚未完成",
  );
  const explanation = page.locator(".execution-incomplete-response").last();
  await expect(explanation.locator("pre")).toBeHidden();
  await explanation.getByText("查看本轮说明", { exact: true }).click();
  await expect(explanation.locator("pre")).toHaveText(text);
  await expect(page.locator(".execution-final")).toHaveCount(0);
  await expect(page.locator("#execution-feedback")).toContainText("尚未通过");
  state.error = null;
  state.status = "completed";
  await expect(page.locator("#execution-focus-label")).toHaveText("本轮已完成");
  return {
    passed: true,
    host_snapshot_fixture: true,
    explanation_preserved: true,
    failed_status_retained: true,
    external_model_calls: 0,
  };
}
