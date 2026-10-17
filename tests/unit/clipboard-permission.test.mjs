import { test } from "node:test";
import assert from "node:assert/strict";
import { createClipboardPermission } from "../../desktop/clipboard-permission.cjs";

test("only the current private app main document gets clipboard write permission", () => {
  let contents = {},
    origin = "http://127.0.0.1:34567";
  const allowed = createClipboardPermission(
    () => contents,
    () => origin,
  );
  const details = { isMainFrame: true };
  const write = "clipboard-sanitized-write";
  assert.equal(allowed(contents, write, origin + "/", details), true);
  for (const permission of [
    "clipboard-read",
    "deprecated-sync-clipboard-read",
    "media",
    "notifications",
    "unknown",
  ])
    assert.equal(allowed(contents, permission, origin, details), false);
  for (const url of [
    "null",
    "",
    "file:///app.html",
    "https://example.org",
    "http://127.0.0.1:34568",
    "http://127.0.0.1:34567.example.org",
    "http://127.0.0.1:34567@example.org",
  ])
    assert.equal(allowed(contents, write, url, details), false, url);
  assert.equal(allowed(contents, write, origin, { isMainFrame: false }), false);
  assert.equal(allowed(contents, write, origin, undefined), false);
  assert.equal(allowed(null, write, origin, details), false);
  assert.equal(allowed({}, write, origin, details), false);
  const old = contents;
  contents = {};
  assert.equal(allowed(old, write, origin, details), false);
  origin = "";
  assert.equal(
    allowed(contents, write, "http://127.0.0.1:34567", details),
    false,
  );
});
