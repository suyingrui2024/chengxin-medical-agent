import { expect } from "@playwright/test";
import { createServer } from "node:http";
import { once } from "node:events";

export async function createPatientCalculationModel({
  capabilityId = "calculate.units",
  inputForText,
} = {}) {
  const requests = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    const context = JSON.parse(body.messages[1].content),
      text = context.latest_user_text;
    const command = {
      tool: "run_capability",
      arguments: {
        id: capabilityId,
        input: inputForText
          ? inputForText(text)
          : text.includes("1.25kg")
            ? { value: "1.25", from_unit: "kg", to_unit: "g" }
            : {},
        evidence: text,
      },
    };
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        model: "fixture",
        choices: [
          {
            message: { role: "assistant", content: JSON.stringify(command) },
            finish_reason: "stop",
          },
        ],
        usage: { prompt_tokens: 10, completion_tokens: 10 },
      }),
    );
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return {
    endpoint: `http://127.0.0.1:${server.address().port}/v1`,
    requests,
    async close() {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

export async function verifyPatientCalculation(
  page,
  model,
  { shots, mobile = false } = {},
) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const original = await page.locator("#active-question h2").textContent();
  const first = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/sessions\/[^/]+\/messages$/.test(response.url()),
  );
  await page
    .getByRole("textbox", { name: "输入你的描述" })
    .fill("请把1.25kg换算成g");
  await page.getByRole("button", { name: "发送回答" }).click();
  const initial = await (await first).json(),
    identity = initial.state.session_id;
  const form = page.locator("#active-question .calculation-form");
  await expect(form).toBeVisible();
  expect(initial.state.calculations).toEqual([]);
  expect(model.requests).toHaveLength(1);
  await expect(form.getByLabel("数值", { exact: true })).toHaveValue("1.25");
  await expect(form.getByLabel("原单位", { exact: true })).toHaveValue("kg");
  await expect(form.getByLabel("目标单位", { exact: true })).toHaveValue("g");
  await form.getByLabel("数值", { exact: true }).fill("2");
  await page.getByRole("button", { name: "展开对话记录", exact: true }).click();
  await page.getByRole("button", { name: "返回当前问题", exact: true }).click();
  await expect(form.getByLabel("数值", { exact: true })).toHaveValue("2");
  await form.getByLabel("目标单位", { exact: true }).selectOption("mL");
  await form.getByRole("button", { name: "核对后计算", exact: true }).click();
  await expect(
    page.locator("#active-question .calculation-feedback"),
  ).toContainText("不能直接互换");
  await expect(form.getByLabel("数值", { exact: true })).toHaveValue("2");
  await expect(form.getByLabel("目标单位", { exact: true })).toHaveValue("mL");
  expect(model.requests).toHaveLength(1);
  await form.getByLabel("目标单位", { exact: true }).selectOption("g");
  if (shots)
    await page.screenshot({
      path: `${shots}/33-patient-calculation-input.png`,
    });
  await page.evaluate(() => {
    const original = window.fetch;
    window.__patientCalculationFault = { committed: false };
    window.fetch = async function (...args) {
      const [url, init] = args;
      if (
        typeof url === "string" &&
        /\/api\/sessions\/[^/]+\/messages$/.test(url) &&
        init?.body &&
        !window.__patientCalculationFault.committed
      ) {
        const body = JSON.parse(init.body);
        if (
          body.choice_value === "confirm_calculation" &&
          body.calculation.input.to_unit === "g"
        ) {
          const response = await original.apply(this, args);
          if (response.status !== 200)
            throw new Error("Fixture confirmation was not accepted");
          await response.arrayBuffer();
          window.__patientCalculationFault = { committed: true, body };
          throw new TypeError(
            "Synthetic response loss after committed confirmation",
          );
        }
      }
      return original.apply(this, args);
    };
  });
  await form.getByRole("button", { name: "核对后计算", exact: true }).click();
  const result = page.locator("#active-question .patient-calculation-result");
  await expect(result.getByRole("heading")).toHaveText("2000 g");
  await expect(result).toContainText("输入已核对");
  await expect(result).toContainText("实际测量质量未评价");
  const snapshot = await page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
    identity,
  );
  const { committed: lost, body: confirmedBody } = await page.evaluate(
    () => window.__patientCalculationFault,
  );
  expect(snapshot.state.calculations).toHaveLength(1);
  expect(snapshot.state.facts).toEqual({});
  expect(snapshot.state.calculations[0].confirmation.original_input.value).toBe(
    "1.25",
  );
  expect(
    snapshot.state.calculations[0].confirmation.confirmed_input.value,
  ).toBe("2");
  const duplicate = await page.evaluate(
    async ({ identity, body }) =>
      (
        await fetch(`/api/sessions/${identity}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      ).status,
    { identity, body: confirmedBody },
  );
  expect(duplicate).toBe(409);
  expect(model.requests).toHaveLength(1);
  expect(lost).toBe(true);
  if (shots)
    await page.screenshot({
      path: `${shots}/34-patient-calculation-result.png`,
    });
  await result.getByRole("button", { name: "继续问诊", exact: true }).click();
  await expect(page.locator("#active-question h2")).toHaveText(original);
  expect(model.requests).toHaveLength(1);
  await page.locator("#new-session").click();
  await page
    .getByRole("textbox", { name: "输入你的描述" })
    .fill("我想换算单位，但还没有提供数字和单位");
  await page.getByRole("button", { name: "发送回答" }).click();
  await expect(form).toBeVisible();
  for (const name of ["数值", "原单位", "目标单位"])
    await expect(form.getByLabel(name, { exact: true })).toHaveValue("");
  expect(model.requests).toHaveLength(2);
  if (mobile) {
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      form.getByRole("button", { name: "核对后计算", exact: true }),
    ).toBeVisible();
    const control = await form
      .getByRole("button", { name: "核对后计算", exact: true })
      .boundingBox();
    const area = await page.locator("#active-question").boundingBox();
    expect(control.y + control.height).toBeLessThanOrEqual(
      area.y + area.height,
    );
    const width = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      viewport: innerWidth,
    }));
    expect(width.scroll).toBeLessThanOrEqual(width.viewport);
    if (shots)
      await page.screenshot({
        path: `${shots}/35-mobile-calculation-input.png`,
      });
    await page.setViewportSize({ width: 1440, height: 1080 });
  }
  await page.reload();
  await page.locator("#recent-sessions .recent-item").first().click();
  await expect(form).toBeVisible();
  for (const name of ["数值", "原单位", "目标单位"])
    await expect(form.getByLabel(name, { exact: true })).toHaveValue("");
  await form.getByLabel("数值", { exact: true }).fill("0.0005");
  await form.getByLabel("原单位", { exact: true }).selectOption("L");
  await form.getByLabel("目标单位", { exact: true }).selectOption("mL");
  await form.getByRole("button", { name: "核对后计算", exact: true }).click();
  await expect(result.getByRole("heading")).toHaveText("0.5 mL");
  expect(model.requests).toHaveLength(2);
  expect(errors).toEqual([]);
}
