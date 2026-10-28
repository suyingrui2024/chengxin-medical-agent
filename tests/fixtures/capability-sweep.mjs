import { createServer } from "node:http";
import { once } from "node:events";

/**
 * A stand-in model that always returns the capability the patient asked
 * for by name. The sweep is not testing selection — that is
 * `evaluate_capability_selection.py` — it is testing that every
 * patient-facing capability can actually be rendered, filled and scored
 * in a browser.
 */
export async function createSweepModel(catalog) {
  const requests = [];
  const server = createServer(async (request, response) => {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    const context = JSON.parse(body.messages[1].content);
    const text = context.latest_user_text || "";

    // Longest alias wins, so "C-ACT" is not matched by "ACT".
    let best = null;
    for (const item of catalog) {
      for (const alias of item.aliases) {
        if (
          text.includes(alias) &&
          (!best || alias.length > best.alias.length)
        ) {
          best = { id: item.id, alias };
        }
      }
    }

    const command = {
      tool: "run_capability",
      arguments: {
        id: best ? best.id : "calculate.bmi",
        input: {},
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

/** Fill every scale item with its first option. */
export async function answerScale(form) {
  const fieldsets = await form.locator("fieldset").all();
  for (const fieldset of fieldsets) {
    await fieldset.locator("input[type=radio]").first().check();
  }
  return fieldsets.length;
}

/**
 * Fill every formula field from a sample the engine already accepted.
 *
 * Deriving values here (midpoint of each input's own range) looked
 * reasonable and was wrong for additive formulas: nine 0-100 burn-area
 * inputs gave 450, far outside the declared 0-100 output, and the sweep
 * then blamed the product for rejecting it. The sample is computed
 * offline by `generate_patient_capability_fixture.py`, which tries
 * fractions until the real engine returns an in-range result.
 */
export async function answerFormula(form, sample) {
  const fields = await form.locator(".formula-field").all();
  const values = Object.values(sample || {});
  for (const [index, field] of fields.entries()) {
    const value = values[index];
    const select = field.locator("select");
    if (await select.count()) {
      await select.selectOption(String(value));
      continue;
    }
    await field.locator("input").fill(String(value));
  }
  return fields.length;
}
