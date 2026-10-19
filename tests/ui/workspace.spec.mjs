import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const shots = "docs/assets/ui";
test.beforeAll(async () => {
  await mkdir(shots, { recursive: true });
});

test("patient conversation, self-check, history, evidence and export use actual server state", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "不用一次说完整。" }),
  ).toBeVisible();
  await page.screenshot({
    path: `${shots}/01-welcome-desktop.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  async function answer(text) {
    await input.fill(text);
    await page.getByRole("button", { name: "发送回答" }).click();
    await expect(page.locator("#busy-indicator")).toBeHidden();
  }
  await answer("32岁");
  await answer("咳嗽发烧两天");
  await page.getByRole("button", { name: "都没有", exact: true }).click();
  await answer("两天前逐渐出现");
  await page.getByRole("button", { name: "变化不大", exact: true }).click();
  await answer("5分");
  await answer("喉咙痛");
  await answer("没有");
  await answer("干咳，没有咳血");
  await answer("能正常喝水");
  await expect(
    page.getByRole("heading", { name: "测一次腋下体温" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(page.getByText("2 / 3")).toBeVisible();
  await expect(page.locator("#active-task .task-image")).toHaveAttribute(
    "src",
    "/temperature.svg#step-2",
  );
  await page.getByRole("button", { name: "展开对话记录" }).click();
  await expect(page.locator("#active-task")).toBeHidden();
  await page.getByRole("button", { name: "返回当前任务" }).click();
  await expect(page.getByText("2 / 3")).toBeVisible();
  await page.screenshot({
    path: `${shots}/02-consultation-self-check.png`,
    fullPage: true,
  });
  await answer("380");
  await expect(page.locator("#active-task")).toBeVisible();
  await expect(page.locator("#active-task .task-feedback")).toBeVisible();
  await expect(page.locator("#messages")).not.toContainText("380 ℃");
  await expect(page.getByText("2 / 3")).toBeVisible();
  await answer("38.2℃");
  await answer("没有");
  await answer("不知道");
  await answer("没有");
  await answer("想整理信息去就医");
  await expect(
    page.getByRole("heading", { name: "本次问诊摘要" }).last(),
  ).toBeVisible();
  const summary = page.locator(".summary-card").last();
  await expect(summary.locator(".unknown-items")).toContainText("过敏信息");
  await summary
    .locator(".summary-item")
    .filter({ hasText: "已做处理/用药" })
    .getByRole("button", { name: "更正", exact: true })
    .click();
  await page.getByLabel("更正「用药与处理」").fill("喝了温水");
  await page.getByRole("button", { name: "保存并重新核对" }).click();
  await expect(page.locator("#edit-dialog")).toBeHidden();
  await expect(page.locator(".summary-card").last()).toContainText("喝了温水");
  await expect
    .poll(() =>
      page.locator("#conversation-scroll").evaluate((node) => node.scrollTop),
    )
    .toBe(0);
  await expect(
    page.locator(".summary-card").first().locator(".summary-edit").first(),
  ).toBeDisabled();
  await page.screenshot({
    path: `${shots}/09-structured-summary.png`,
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "确认摘要并结束本次问诊", exact: true })
    .click();
  await expect(page.locator("#composer")).toBeHidden();
  await page.getByLabel("界面模式").selectOption("developer");
  await page.getByRole("button", { name: "查看本次运行记录" }).click();
  await expect(
    page.getByRole("heading", { name: "看清每一步，才能继续改进。" }),
  ).toBeVisible();
  await expect(page.locator("#evidence-list")).toContainText("32 岁");
  await page.getByLabel("筛选事件").selectOption("fact");
  await page.locator(".event-row").first().locator("summary").click();
  await expect(page.locator(".event-row").first().locator("pre")).toBeVisible();
  await page.screenshot({ path: `${shots}/03-inspection.png`, fullPage: true });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出本次记录" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^chengxin-session-/);
  await page.locator('.nav-item[data-view="history"]').click();
  await expect(
    page.locator(".history-card").filter({ hasText: "咳嗽发烧两天" }).first(),
  ).toBeVisible();
  await page
    .locator(".history-card")
    .filter({ hasText: "咳嗽发烧两天" })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "本次问诊摘要" }).last(),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("structured components isolate errors and keep patient text inert", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  await page.evaluate(async () => {
    const { createRenderer } = await import("/structured-ui.js");
    const renderer = createRenderer();
    renderer.register(
      "broken-test",
      () => {
        throw new Error("invalid plugin output");
      },
      () => document.createElement("div"),
    );
    const target = document.createElement("section");
    target.id = "component-test";
    target.append(
      renderer.renderEnvelope({
        schema_version: "medical-ui/v1",
        blocks: [
          { id: "unknown", type: "unregistered", data: {} },
          {
            id: "bad-calculation",
            type: "calculation",
            data: { value: "1250" },
          },
          { id: "broken", type: "broken-test", data: {} },
          null,
          {
            id: "safe-message",
            type: "message",
            data: {
              kind: "question",
              text: '<img src=x onerror="alert(1)">保留原文',
            },
          },
        ],
      }),
    );
    target.append(
      renderer.renderEnvelope({ schema_version: "future/v99", blocks: [] }),
    );
    document.body.append(target);
  });
  await expect(
    page.locator("#component-test .structured-fallback"),
  ).toHaveCount(5);
  await expect(page.locator("#component-test .bubble")).toContainText(
    "保留原文",
  );
  await expect(page.locator('#component-test img[src="x"]')).toHaveCount(0);
});

test("unknown result components produce a visible fallback instead of disappearing", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("界面模式").selectOption("developer");
  await page.locator('.nav-item[data-view="capabilities"]').click();
  await page.route("**/api/capabilities/preview", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.presentation.blocks[0].type = "future-calculation";
    await route.fulfill({ response, json: payload });
  });
  await page.getByRole("button", { name: "执行并查看公式" }).click();
  await expect(
    page.locator("#capability-test-result .structured-fallback"),
  ).toBeVisible();
});

test("an emergency notice remains visible through the structured renderer", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page
    .getByRole("textbox", { name: "输入你的描述" })
    .fill("他现在叫不醒");
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(page.locator(".structured-notice")).toContainText("立即急救");
  const action = page.locator(".structured-notice .notice-action");
  await expect(action).toBeVisible();
  await expect(action).toContainText("120");
  await expect(action).toContainText("不要继续等待本次对话");
  await expect(page.locator(".structured-notice details")).not.toHaveAttribute(
    "open",
    "",
  );
  await expect(page.locator("#composer")).toBeHidden();
  await expect(page.locator(".structured-fallback")).toHaveCount(0);
});

test("settings are real, keep old sessions, and never return credentials", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByLabel("界面模式").selectOption("developer");
  await page.getByRole("button", { name: "模型与运行配置" }).click();
  await expect(page.locator("#settings-dialog")).toBeVisible();
  await page.getByRole("radio", { name: /DSH 问诊/ }).check();
  await expect(page.locator("#protocol-hint")).toContainText("Messages");
  await page.screenshot({ path: `${shots}/04-settings.png`, fullPage: true });
  await page.getByRole("radio", { name: /直接模型/ }).check();
  await page.getByLabel("服务地址").fill("file:///tmp/nope");
  await page.getByLabel("模型 ID").fill("fixture");
  await page.getByLabel("API 密钥").fill("ui-test-only-secret");
  await page.getByRole("button", { name: "应用配置" }).click();
  await expect(page.locator("#settings-feedback")).toContainText("HTTP(S)");
  await page.getByRole("radio", { name: /离线演示/ }).check();
  await page.getByRole("button", { name: "测试连接" }).click();
  await expect(page.locator("#settings-feedback")).toContainText("已就绪");
  await page.getByRole("button", { name: "应用配置" }).click();
  await expect(page.locator("#settings-dialog")).not.toBeVisible();
  const settings = await (await request.get("/api/settings")).json();
  expect(JSON.stringify(settings)).not.toContain("ui-test-only-secret");
  await expect(
    page.getByRole("button", { name: "我自己", exact: true }),
  ).toBeVisible();
});

test("task library preview cannot write observations and has step navigation", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByLabel("界面模式").selectOption("developer");
  await page.locator('.nav-item[data-view="tasks"]').click();
  await expect(
    page.getByRole("heading", { name: "用图示，把下一步说清楚。" }),
  ).toBeVisible();
  const before = await (await request.get("/api/sessions")).json();
  await page.screenshot({
    path: `${shots}/05-task-library.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "查看图示与步骤" }).first().click();
  await expect(
    page.getByText("这是素材预览，不会记录结果或改变问诊进度。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(page.getByText("2 / 3")).toBeVisible();
  await page.getByRole("button", { name: "关闭预览" }).click();
  const after = await (await request.get("/api/sessions")).json();
  expect(after.sessions.length).toBe(before.sessions.length);
});

test("mobile layout has no horizontal overflow and exposes records and navigation", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "开始问诊", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `${shots}/06-welcome-mobile.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByLabel("界面模式").selectOption("developer");
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.locator('.nav-item[data-view="tasks"]').click();
  await expect(
    page.getByRole("heading", { name: "用图示，把下一步说清楚。" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看图示与步骤" }).first().click();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(page.locator("#task-preview .task-image")).toHaveAttribute(
    "src",
    "/temperature.svg#step-2",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: `${shots}/10-self-check-mobile.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭预览" }).click();
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.locator('.nav-item[data-view="consultation"]').click();
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "问诊记录 0" }).click();
  await expect(
    page.getByRole("heading", { name: "已记录的信息" }),
  ).toBeVisible();
});

test("editing a fact updates the actual case and retains the previous value", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("textbox", { name: "输入你的描述" }).fill("32岁");
  await page.getByRole("button", { name: "发送回答" }).click();
  const age = page.locator(".fact-row").filter({ hasText: "年龄" });
  await expect(age).toContainText("32 岁");
  await age.getByRole("button", { name: "更正", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "更正记录" })).toBeVisible();
  await page.getByLabel("更正「年龄」").fill("40岁");
  const responsePromise = page.waitForResponse(
    (response) => response.request().method() === "PATCH",
  );
  await page.getByRole("button", { name: "保存并重新核对" }).click();
  const data = await (await responsePromise).json();
  await expect(page.locator("#edit-dialog")).toBeHidden();
  await expect(age).toContainText("40 岁");
  expect(data.state.facts.age_years.value).toBe(40);
  expect(
    data.state.history.some(
      (fact) => fact.key === "age_years" && fact.value === 32,
    ),
  ).toBe(true);
});

test("a stale fact editor preserves the draft and refreshes the current record", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/sessions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const { session_id: id } = await (await created).json();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("textbox", { name: "输入你的描述" }).fill("32岁");
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(page.locator("#busy-indicator")).toBeHidden();
  const age = page.locator(".fact-row").filter({ hasText: "年龄" });
  await age.getByRole("button", { name: "更正", exact: true }).click();
  await page.getByLabel("更正「年龄」").fill("40岁");
  const before = await (await request.get(`/api/sessions/${id}`)).json();
  const updated = await request.patch(`/api/sessions/${id}/facts/age_years`, {
    data: {
      text: "41岁",
      expected_fact_id: before.state.facts.age_years.fact_id,
    },
  });
  expect(updated.ok()).toBe(true);
  await page.getByRole("button", { name: "保存并重新核对" }).click();
  await expect(page.locator("#edit-feedback")).toContainText("这项记录已更新");
  await expect(page.getByLabel("更正「年龄」")).toHaveValue("40岁");
  await expect(age).toContainText("41 岁");
  const current = await (
    await request.get(`/api/sessions/${id}/snapshot`)
  ).json();
  expect(current.state.facts.age_years.value).toBe(41);
  expect(
    current.events.filter((e) => e.event_type === "turn.agent_reply").at(-1)
      .payload.reply.action_id,
  ).toBe(`${id}:${current.state.revision}`);
  await page.getByRole("button", { name: "关闭更正", exact: true }).click();
  await age.getByRole("button", { name: "更正", exact: true }).click();
  await page.getByLabel("更正「年龄」").fill("42岁");
  await page.getByRole("button", { name: "保存并重新核对" }).click();
  await expect(page.locator("#edit-dialog")).toBeHidden();
  await expect(age).toContainText("42 岁");
});

test("changing the consultation subject opens a separate case and retains the original", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/sessions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const { session_id: originalId } = await (await created).json();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  const subject = page.locator(".fact-row").filter({ hasText: "咨询对象" });
  await subject.getByRole("button", { name: "更正", exact: true }).click();
  await expect(page.locator("#edit-hint")).toContainText("本次咨询对象已固定");
  await expect(
    page.getByRole("button", { name: "保存并重新核对" }),
  ).toBeHidden();
  const next = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/sessions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "为另一位家人开始问诊" }).click();
  const fresh = await (await next).json();
  expect(fresh.session_id).not.toBe(originalId);
  expect(fresh.state.facts).toEqual({});
  const old = await (await request.get(`/api/sessions/${originalId}`)).json();
  expect(old.state.facts.consult_for.value).toBe("self");
});

test("a lost response recovers persisted progress and patient markup stays plain text", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  await input.fill("32岁");
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(page.locator("#busy-indicator")).toBeHidden();
  let savedSession;
  await page.route(
    "**/api/sessions/*/messages",
    async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      savedSession = body.state.session_id;
      await route.abort("failed");
    },
    { times: 1 },
  );
  const text = '咳嗽 <img src=x onerror="alert(1)">';
  await input.fill(text);
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(page.locator("#busy-indicator")).toBeHidden();
  await expect(page.locator(".message.user").last()).toContainText(text);
  expect(await page.locator('.message.user img[src="x"]').count()).toBe(0);
  const trace = await (
    await request.get(`/api/sessions/${savedSession}/events`)
  ).json();
  expect(
    trace.events.filter(
      (event) =>
        event.event_type === "turn.user_message" && event.payload.text === text,
    ),
  ).toHaveLength(1);
  await expect(input).toBeEnabled();
});
