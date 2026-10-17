import { test, expect } from "@playwright/test";
import {
  createConsultationSubjectsModel,
  verifyConsultationSubjects,
} from "../fixtures/consultation-subjects.mjs";

test("unsubmitted patient text stays with its original session", async ({
  page,
  request,
}) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
  await page.goto("/");
  const create = async (button) => {
    const response = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
    );
    await button.click();
    return (await response).json();
  };
  const first = await create(
    page.getByRole("button", { name: "开始问诊", exact: true }),
  );
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  await input.fill("父亲昨晚用过药物甲，尚未发送。");
  const second = await create(page.locator("#new-session"));
  await expect(input).toHaveValue("");
  await input.fill("母亲的另一条未发送描述。");
  await page
    .locator(
      `#recent-sessions .recent-item[data-session-id="${first.session_id}"]`,
    )
    .click();
  await expect(input).toHaveValue("父亲昨晚用过药物甲，尚未发送。");
  await page
    .locator(
      `#recent-sessions .recent-item[data-session-id="${second.session_id}"]`,
    )
    .click();
  await expect(input).toHaveValue("母亲的另一条未发送描述。");
  for (const id of [first.session_id, second.session_id]) {
    const snapshot = await (
      await request.get(`/api/sessions/${id}/snapshot`)
    ).json();
    expect(snapshot.state.facts).toEqual({});
    expect(
      snapshot.events.filter(
        (event) => event.event_type === "turn.user_message",
      ),
    ).toHaveLength(0);
  }
});

test("choosing an answer does not silently submit or erase a different composer draft", async ({
  page,
  request,
}) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
  await page.goto("/");
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  const id = (await (await created).json()).session_id;
  const draft = "我32岁还有咳嗽，这段尚未发送。";
  await page.getByRole("textbox", { name: "输入你的描述" }).fill(draft);
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await expect(page.locator("#active-question h2")).toContainText("多大");
  await expect(page.getByRole("textbox", { name: "输入你的描述" })).toHaveValue(
    draft,
  );
  const snapshot = await (
    await request.get(`/api/sessions/${id}/snapshot`)
  ).json();
  const inputs = snapshot.events.filter(
    (event) => event.event_type === "turn.user_message",
  );
  expect(inputs).toHaveLength(1);
  expect(inputs[0].payload.text).toBe("我自己");
  expect(JSON.stringify(inputs)).not.toContain(draft);
});

for (const mobile of [false, true])
  test(`consultation subjects stay separate and new-case text remains editable${mobile ? " on a narrow screen" : ""}`, async ({
    page,
    request,
  }) => {
    const model = await createConsultationSubjectsModel();
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "subject-ui-only",
            },
          })
        ).ok(),
      ).toBe(true);
      await page.goto("/");
      await verifyConsultationSubjects(page, model, {
        shots: `docs/assets/ui/68-${mobile ? "mobile" : "wide"}-consultation-subject`,
        mobile,
      });
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
