import { test } from "@playwright/test";
import { reachSelfCheck } from "../fixtures/self-check-drafts.mjs";
import { verifyLocationLayout } from "../fixtures/location-layout.mjs";
import { shotPath } from "../fixtures/shots.mjs";

for (const [label, width, height] of [
  ["wide", 1440, 1080],
  ["mobile", 390, 844],
  ["short", 900, 648],
])
  test(`location controls have separate readable targets on ${label}`, async ({
    page,
    request,
  }) => {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await page.setViewportSize({ width, height });
    await page.goto("/");
    const id = await reachSelfCheck(page);
    const result = await verifyLocationLayout(page, id, {
      shot: shotPath(`74-${label}-location-layout.png`),
    });
    await test.info().attach("location-targets", {
      body: JSON.stringify(result, null, 2),
      contentType: "application/json",
    });
  });
