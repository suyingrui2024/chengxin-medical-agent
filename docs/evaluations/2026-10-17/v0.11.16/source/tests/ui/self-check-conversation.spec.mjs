import { test, expect } from "@playwright/test";
import {
  createSelfCheckConversationModel,
  verifySelfCheckConversation,
} from "../fixtures/self-check-conversation.mjs";

for (const [label, width, height, option, manual] of [
  ["wide", 1440, 1080, "head", false],
  ["mobile", 390, 844, "head", true],
  ["short", 900, 648, "other", false],
])
  test(`self-check conversation resumes and confirms sources on ${label}`, async ({
    page,
    request,
  }) => {
    const model = await createSelfCheckConversationModel(option);
    try {
      expect(
        (
          await request.post("/api/settings", {
            data: {
              backend: "compatible",
              base_url: model.endpoint,
              model: "fixture",
              api_key: "local-task-conversation",
            },
          })
        ).ok(),
      ).toBe(true);
      await page.setViewportSize({ width, height });
      await page.goto("/");
      await verifySelfCheckConversation(page, model, {
        manual,
        shot: `docs/assets/ui/75-${label}-self-check-conversation.png`,
      });
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
