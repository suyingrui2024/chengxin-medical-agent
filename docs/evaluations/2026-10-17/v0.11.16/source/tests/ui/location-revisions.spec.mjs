import { test, expect } from "@playwright/test";
import { createLocationAnswersModel } from "../fixtures/location-answers.mjs";
import { verifyLocationRevision } from "../fixtures/location-revisions.mjs";
import { verifyLocationAnswers } from "../fixtures/location-answers.mjs";

for (const [label, width, height] of [
  ["wide", 1440, 1080],
  ["mobile", 390, 844],
  ["short", 900, 648],
]) {
  test(`confirmed location revision cancels safely and resumes the question on ${label}`, async ({
    page,
    request,
  }) => {
    const model = await createLocationAnswersModel();
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "location-revision-fixture",
            },
          })
        ).ok(),
      ).toBe(true);
      await page.setViewportSize({ width, height });
      await page.goto("/");
      await verifyLocationRevision(page, model, {
        shot: `docs/assets/ui/78-${label}-location-revision.png`,
      });
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
}

test("location revision returns an unsent severity selection to the original question", async ({
  page,
  request,
}) => {
  const model = await createLocationAnswersModel(false, {
    nextQuestion: "severity",
  });
  try {
    await request.post("/api/settings", {
      data: {
        backend: "compatible",
        base_url: model.endpoint,
        model: "fixture",
        api_key: "location-revision-degree",
      },
    });
    await page.setViewportSize({ width: 900, height: 648 });
    await page.goto("/");
    await verifyLocationAnswers(page, model);
    await page.getByRole("button", { name: "5 分", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "确认 5 分", exact: true }),
    ).toBeEnabled();
    await page.locator("#toggle-facts").click();
    await page.locator("#fact-list .location-revision-start").click();
    await expect(page.locator(".location-answer-review")).toBeVisible();
    await page
      .getByRole("button", { name: "取消修改，保留原记录", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "确认 5 分", exact: true }),
    ).toBeEnabled();
    await expect(
      page.locator('.severity-options [aria-pressed="true"]'),
    ).toHaveText("5");
    await expect(
      page.getByRole("textbox", { name: "输入你的描述" }),
    ).toHaveValue("这段用药补充还没有发送。");
    expect(model.requests).toHaveLength(4);
  } finally {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await model.close();
  }
});
