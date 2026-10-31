import base from "./playwright.config.mjs";

/**
 * The CI run is the whole browser suite, not a hand-picked list. The
 * previous three-file list is exactly how 16 appearance tests stayed red
 * for five days without anyone noticing.
 *
 * There is no `grepInvert` here any more, on purpose. The last one named
 * eleven tests that still expected the dialogue the engine ran before
 * d1568d6 reordered questions by hypothesis. Two things fixed them:
 *
 *   - the scripted models now obey the engine's machine-readable feedback
 *     (tests/fixtures/model-feedback.mjs), so a rejected proposal no
 *     longer desyncs the whole queue;
 *   - `summary-card[data-risk-level="urgent"]` carries the urgent accent
 *     itself, instead of borrowing it from `.summary-route.urgent` — a
 *     section that is deliberately not rendered once the conclusion sheet
 *     takes over the routing guidance.
 *
 * An exclusion list is a place for failures to hide. If a test has to be
 * excluded again, name it here with the reason and a plan to remove it.
 */
export default base;
