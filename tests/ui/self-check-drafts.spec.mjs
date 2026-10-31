import { test, expect } from "@playwright/test";
import {
  verifyLocationDraft,
  verifyTemperatureProgress,
  reachSelfCheck,
} from "../fixtures/self-check-drafts.mjs";
import { degreeSnapshot } from "../fixtures/visual-answer-drafts.mjs";
import { shotPath } from "../fixtures/shots.mjs";

for (const mobile of [false, true])
  test(`unconfirmed location and steps survive switching sessions${mobile ? " on mobile" : ""}`, async ({
    page,
    request,
  }) => {
    await request.post("/api/settings", { data: { backend: "demo" } });
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await verifyLocationDraft(page, {
      shot: shotPath(`72-${mobile ? "mobile" : "wide"}-location-draft.png`),
    });
  });

test("invalid temperature feedback keeps the same task progress without recording a reading", async ({
  page,
  request,
}) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
  await page.goto("/");
  await verifyTemperatureProgress(page);
});

test("new task occurrences reset steps while same occurrences and sessions stay separate", async ({
  page,
  request,
}) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
  await page.goto("/");
  const id = await reachSelfCheck(page);
  const snapshot = await degreeSnapshot(page, id);
  const task = snapshot.events
    .filter((e) => e.event_type === "turn.agent_reply")
    .at(-1).payload.reply.task;
  const states = await page.evaluate(
    async ({ task, id }) => {
      const { createRenderer } = await import("/structured-ui.js");
      const renderer = createRenderer();
      const render = (interaction, sessionId = id, readOnly = false) =>
        renderer.render(
          {
            id: "task-test",
            type: "self_check",
            data: { ...task, interaction_id: interaction },
          },
          { sessionId, readOnly, onAnswer: () => {}, isBusy: () => false },
        );
      const first = render("offer:first");
      document.body.append(first);
      first.querySelectorAll(".step-controls button")[1].click();
      const text = (card) =>
        card.querySelector(".step-progress > span").textContent;
      const same = render("offer:first"),
        next = render("offer:second"),
        other = render("offer:first", "another-session"),
        inert = render("offer:first", id, true);
      inert.querySelectorAll(".step-controls button").forEach((button) => {
        button.disabled = false;
        button.click();
      });
      const result = [first, same, next, other, inert].map(text);
      first.remove();
      return result;
    },
    { task, id },
  );
  expect(states).toEqual(["2 / 2", "2 / 2", "1 / 2", "1 / 2", "2 / 2"]);
  expect((await degreeSnapshot(page, id)).events).toEqual(snapshot.events);
});
