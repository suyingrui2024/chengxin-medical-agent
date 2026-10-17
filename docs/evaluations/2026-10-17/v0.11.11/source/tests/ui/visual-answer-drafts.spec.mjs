import { test, expect } from "@playwright/test";
import {
  reachDegree,
  degreeSnapshot,
  verifyDegreeDraft,
} from "../fixtures/visual-answer-drafts.mjs";

for (const mobile of [false, true])
  test(`visual degree confirms one answer and preserves independent text${mobile ? " on a narrow screen" : ""}`, async ({
    page,
    request,
  }) => {
    await request.post("/api/settings", { data: { backend: "demo" } });
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await verifyDegreeDraft(page, {
      shot: `docs/assets/ui/70-${mobile ? "mobile" : "wide"}-degree-draft.png`,
    });
  });

test("pending visual choices stay with their session and never submit on return", async ({
  page,
  request,
}) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
  await page.goto("/");
  const id = await reachDegree(page);
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  await input.fill("另外写的补充尚未发送。");
  await page.getByRole("button", { name: "5 分", exact: true }).click();
  const before = await degreeSnapshot(page, id);
  const pending = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/api/sessions"),
  );
  await page.locator("#new-session").click();
  const next = (await (await pending).json()).session_id;
  await expect(input).toHaveValue("");
  expect((await degreeSnapshot(page, next)).state.facts).toEqual({});
  await page.locator(`#recent-sessions [data-session-id="${id}"]`).click();
  await expect(input).toHaveValue("另外写的补充尚未发送。");
  await expect(
    page.getByRole("button", { name: "确认 5 分", exact: true }),
  ).toBeEnabled();
  await expect(
    page.locator(".severity-options [aria-pressed='true']"),
  ).toHaveText("5");
  expect((await degreeSnapshot(page, id)).events).toEqual(before.events);
});

test("read-only and busy degree cards reject forced programmatic actions", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { createRenderer } = await import("/structured-ui.js");
    const renderer = createRenderer();
    const calls = [];
    for (const context of [{ readOnly: true }, { isBusy: () => true }, {}]) {
      const card = renderer.render(
        {
          id: "test-degree",
          type: "question",
          data: {
            question_id: "severity",
            canonical: true,
            prompt: "程度？",
            choices: [],
          },
        },
        {
          ...context,
          questionDraft: { degree: 0 },
          ...(Object.keys(context).length
            ? { onConfirmAnswer: (text) => calls.push(text) }
            : {}),
        },
      );
      document.body.append(card);
      const buttons = [
        ...card.querySelectorAll(".severity-options button, .severity-confirm"),
      ];
      if (!buttons.every((button) => button.disabled))
        return { disabled: false, calls };
      buttons.forEach((button) => {
        button.disabled = false;
        button.click();
      });
      card.remove();
    }
    return { disabled: true, calls };
  });
  expect(result).toEqual({ disabled: true, calls: [] });
});
