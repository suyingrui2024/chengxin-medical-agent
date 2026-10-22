import { _electron as electron, test, expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("native priority help interrupts real DSH while the provider is still waiting", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const requests = [];
  const model = createServer(async (request, response) => {
    response.on("error", () => {});
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks).toString()));
    await gate;
    if (response.destroyed) return;
    const proposal = {
      tool: "record_facts",
      arguments: {
        facts: [
          {
            key: "age_years",
            value: 32,
            assertion: "present",
            evidence: "32岁",
          },
        ],
      },
    };
    const events = [
      {
        type: "message_start",
        message: {
          id: "native-interrupt",
          model: "fixture",
          usage: { input_tokens: 10, output_tokens: 1 },
        },
      },
      {
        type: "content_block_start",
        index: 0,
        content_block: { type: "text", text: "" },
      },
      {
        type: "content_block_delta",
        index: 0,
        delta: { type: "text_delta", text: JSON.stringify(proposal) },
      },
      { type: "content_block_stop", index: 0 },
      {
        type: "message_delta",
        delta: { stop_reason: "end_turn" },
        usage: { output_tokens: 10 },
      },
      { type: "message_stop" },
    ];
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    response.end(
      events
        .map(
          (event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
        )
        .join(""),
    );
  });
  model.listen(0, "127.0.0.1");
  await once(model, "listening");
  const endpoint = `http://127.0.0.1:${model.address().port}/anthropic`;
  const env = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: path.join(root, ".runtime/desktop-test-urgent"),
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "dsh",
    MEDAGENT_LLM_BASE_URL: endpoint,
    MEDAGENT_LLM_API_KEY: "native-interrupt-local-only",
    MEDAGENT_LLM_MODEL: "fixture",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  let desktop;
  try {
    desktop = await electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env,
    });
    const page = await desktop.firstWindow();
    await expect(page.getByText("本机服务已连接")).toBeVisible();
    const settings = await page.evaluate(async () =>
      (await fetch("/api/settings")).json(),
    );
    expect(settings.base_url).toBe(endpoint);
    expect(settings.backend).toBe("dsh");
    await page.getByRole("button", { name: "开始问诊", exact: true }).click();
    await page
      .getByRole("textbox", { name: "输入你的描述" })
      .fill("我32岁，咳嗽两天");
    await page.getByRole("button", { name: "发送回答" }).click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].tools || []).toHaveLength(0);
    await expect(page.locator("#message-input")).toBeDisabled();
    await page.getByRole("button", { name: "紧急求助", exact: true }).click();
    await page.getByLabel("现在的情况（选填）").fill("现在需要身边的人协助");
    await page.screenshot({
      path: "docs/assets/ui/40-native-priority-help.png",
      animations: "disabled",
    });
    const start = Date.now();
    await page.getByRole("button", { name: "优先求助", exact: true }).click();
    await expect(page.locator("#phase-label")).toHaveText("立即求助");
    expect(Date.now() - start).toBeLessThan(1500);
    await expect(page.locator("#urgent-help-dialog")).toBeHidden();
    await expect(page.locator(".notice-action")).toBeVisible();
    await expect(page.locator(".notice-action")).toContainText("120");
    await expect(
      page.getByRole("link", { name: "拨打急救电话 120" }),
    ).toHaveAttribute("href", "tel:120");
    await page.screenshot({
      path: "docs/assets/ui/41-native-priority-response.png",
      animations: "disabled",
    });
    release();
    await expect
      .poll(async () =>
        page.evaluate(async () => {
          const list = await (await fetch("/api/sessions")).json();
          const snapshot = await (
            await fetch(`/api/sessions/${list.sessions[0].session_id}/snapshot`)
          ).json();
          return {
            discarded: snapshot.events.some(
              (event) => event.event_type === "agent.decision_discarded",
            ),
            age: Boolean(snapshot.state.facts.age_years),
            screen: Boolean(snapshot.state.facts.emergency_screen),
            risk: snapshot.state.risk.level,
          };
        }),
      )
      .toEqual({
        discarded: true,
        age: false,
        screen: false,
        risk: "emergency",
      });
    expect(requests).toHaveLength(1);
    await expect(page.locator("#messages .emergency")).toHaveCount(1);
  } finally {
    release();
    await desktop?.close();
    model.closeAllConnections();
    await new Promise((resolve) => model.close(resolve));
  }
});
