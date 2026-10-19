import { expect, test } from "@playwright/test";

const summary = (reviewStatus, riskTriggers = []) => ({
  known: [],
  unknown: [],
  unknown_details: [],
  observations: [],
  care_route: "建议今天联系医疗机构。",
  risk_level: riskTriggers.length ? "urgent" : "routine",
  risk_triggers: riskTriggers,
  review_status: reviewStatus,
  return_now_if: ["意识异常或抽搐"],
});

test("summary status distinguishes confirmation from automatic urgent closure", async ({
  page,
}) => {
  await page.goto("/");
  const rendered = await page.evaluate(
    async ({ pending, confirmed, urgent }) => {
      const { createRenderer } = await import("/structured-ui.js");
      const render = (data, readOnly) => {
        const node = createRenderer().render(
          { id: data.review_status, type: "summary", data },
          { readOnly },
        );
        document.body.append(node);
        return {
          text: node.textContent,
          riskTarget: node.querySelector(".summary-risk-triggers")?.textContent,
          editDisabled: node.querySelector(".summary-edit")?.disabled,
        };
      };
      return {
        pending: render(pending, false),
        confirmed: render(confirmed, true),
        urgent: render(urgent, true),
      };
    },
    {
      pending: summary("pending_review"),
      confirmed: summary("confirmed_by_user"),
      urgent: summary("closed_unconfirmed", ["体温达到需要尽快评估的范围"]),
    },
  );

  expect(rendered.pending.text).toContain("待你核对");
  expect(rendered.pending.text).toContain("确认后会结束本次问诊");
  expect(rendered.confirmed.text).toContain("你已确认");
  expect(rendered.urgent.text).toContain("会谈已结束，尚未核对");
  expect(rendered.urgent.riskTarget).toContain("体温达到需要尽快评估的范围");
});

test("emergency notice exposes an immediate call action", async ({ page }) => {
  await page.goto("/");
  const content = await page.evaluate(async () => {
    const { replyEnvelope, createRenderer } = await import("/structured-ui.js");
    const envelope = replyEnvelope({
      message: "请现在拨打 120。",
      kind: "emergency",
      summary: { care_route: "立即急救", triggers: ["严重呼吸困难"] },
    });
    const block = envelope.blocks.at(-1);
    return createRenderer().render(block, {}).textContent;
  });
  expect(content).toContain("现在拨打 120");
  expect(content).toContain("严重呼吸困难");
});

test("correction feedback offers one reachable undo action", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async (data) => {
    const { createRenderer } = await import("/structured-ui.js");
    let undo = null;
    const card = createRenderer().render(
      { id: "correction", type: "summary", data },
      {
        reviewFeedback: {
          message: "已更新“年龄”，请核对受影响的内容。",
          factId: "current-fact",
        },
        onUndoCorrection: (payload) => (undo = payload),
      },
    );
    document.body.append(card);
    const button = card.querySelector(".summary-undo");
    button.click();
    const bounds = button.getBoundingClientRect();
    return {
      label: button.textContent,
      undo,
      width: bounds.width,
      height: bounds.height,
    };
  }, summary("pending_review"));
  expect(result.label).toBe("撤销本次更正");
  expect(result.undo.factId).toBe("current-fact");
  expect(result.width).toBeGreaterThanOrEqual(44);
  expect(result.height).toBeGreaterThanOrEqual(44);
});
