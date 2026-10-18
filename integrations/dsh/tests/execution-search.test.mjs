import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { apply } from "../execution-search.mjs";

test("official search preserves result mapping and binds its internal HTTP request to the initiating session", async () => {
  const requests = [],
    recorded = [];
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    requests.push({ path: req.url, body: JSON.parse(raw) });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        content: [
          {
            type: "web_search_tool_result",
            content: [
              {
                type: "web_search_result",
                url: "https://example.org/fixture",
                title: "Synthetic fixture",
              },
            ],
          },
        ],
      }),
    );
  });
  const previous = Object.fromEntries(
    ["DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY", "CHENGXIN_MODEL"].map((key) => [
      key,
      process.env[key],
    ]),
  );
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  let provider;
  let agent = {
    session: { id: "child-id", append: (...args) => recorded.push(args) },
  };
  try {
    process.env.DEEPSEEK_BASE_URL = `http://127.0.0.1:${server.address().port}/model`;
    process.env.DEEPSEEK_API_KEY = "search-fixture-only";
    process.env.CHENGXIN_MODEL = "fixture";
    apply({
      web: {
        registerSearchProvider: (value) => {
          provider = value;
        },
      },
      agents: { currentInitiator: () => agent },
    });
    assert.equal(provider.available(), true);
    const value = await provider.search({ query: "synthetic fixture" });
    assert.deepEqual(value.sources, [
      { url: "https://example.org/fixture", title: "Synthetic fixture" },
    ]);
    assert.equal(requests[0].path, "/model/sessions/child-id/messages");
    assert.equal(requests[0].body.max_tokens, 2048);
    assert.equal(requests[0].body.tools[0].max_uses, 3);
    assert.equal(recorded[0][0], "web/deepseek-search-llm-request");
    agent = undefined;
    assert.equal(provider.available(), true); // Discovery does not dispatch outside an agent.
    assert.equal(requests.length, 1);
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
