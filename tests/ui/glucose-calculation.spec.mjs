import { test, expect } from "@playwright/test";
import {
  createGlucoseModel,
  verifyGlucoseCalculation,
} from "../fixtures/glucose-calculation.mjs";
import { SHOTS_DIR } from "../fixtures/shots.mjs";

test("glucose renderer rejects missing basis and unsafe links while preserving legacy results", async ({
  page,
  request,
}) => {
  const catalog = await (await request.get("/api/capabilities")).json();
  const preview = async (id, input) => {
    const response = await request.post("/api/capabilities/preview", {
      data: { id, input, expected_revision: catalog.revision },
    });
    expect(response.ok()).toBe(true);
    return (await response.json()).presentation;
  };
  const glucose = await preview("calculate.glucose", {
    value: "180",
    from_unit: "mg/dL",
    to_unit: "mmol/L",
  });
  const legacy = await preview("calculate.units", {
    value: "1.25",
    from_unit: "kg",
    to_unit: "g",
  });
  delete legacy.blocks[0].data.subject_label;
  await page.goto("/");
  await page.evaluate(
    async ({ glucose, legacy }) => {
      const { createRenderer } = await import("/structured-ui.js");
      const renderer = createRenderer();
      const target = document.createElement("section");
      target.id = "glucose-renderer-check";
      target.append(renderer.renderEnvelope(legacy));
      for (const kind of [
        "references",
        "approximate",
        "precision_policy",
        "url",
        "credentials",
      ]) {
        const altered = structuredClone(glucose);
        const data = altered.blocks[0].data;
        if (kind === "url") data.references[0].url = "javascript:alert(1)";
        else if (kind === "credentials")
          data.references[0].url = "https://name:secret@example.org/source";
        else delete data[kind];
        target.append(renderer.renderEnvelope(altered));
      }
      const form = {
        proposal_id: "draft",
        capability_id: "calculate.glucose",
        name: "血糖单位换算",
        version: "0.1.0",
        registry_sha256: "registry",
        source_quote: "血糖180mg/dL",
        source_turn_id: "root",
        feedback: "",
        input: {},
        form_kind: "unit_conversion/v2",
        unit_options: [{ value: "mg/dL", label: "mg/dL" }],
        scope_note: "血糖换算",
      };
      target.append(
        renderer.renderEnvelope({
          schema_version: "medical-ui/v1",
          blocks: [
            { id: "missing-subject", type: "calculation_input", data: form },
          ],
        }),
      );
      const safe = structuredClone(glucose);
      safe.blocks[0].data.references[0].title =
        '<img src=x onerror="alert(1)">原文标题';
      target.append(renderer.renderEnvelope(safe));
      document.body.append(target);
    },
    { glucose, legacy },
  );
  const target = page.locator("#glucose-renderer-check");
  await expect(target.locator(".structured-fallback")).toHaveCount(6);
  await expect(target.locator(".calculation-card h3")).toHaveText([
    "1250 g",
    "≈ 10 mmol/L",
  ]);
  await target
    .locator(".calculation-card")
    .last()
    .getByText("执行依据与限制", { exact: true })
    .click();
  // The injected link is appended last; the capability may carry its own
  // references (units now cites the SI Brochure).
  await expect(
    target.locator(".calculation-references a").last(),
  ).toContainText("原文标题");
  await expect(target.locator('img[src="x"]')).toHaveCount(0);
});

test("sourced glucose conversion uses a scoped form, approximate result and original consultation", async ({
  page,
  request,
}) => {
  const model = await createGlucoseModel();
  try {
    expect(
      (
        await request.post("/api/settings", {
          data: {
            backend: "compatible",
            base_url: model.endpoint,
            model: "fixture",
            api_key: "glucose-ui-only",
          },
        })
      ).ok(),
    ).toBe(true);
    await page.goto("/");
    await verifyGlucoseCalculation(page, model, {
      shots: SHOTS_DIR,
      mobile: true,
    });
  } finally {
    await request.post("/api/settings", { data: { backend: "demo" } });
    await model.close();
  }
});
