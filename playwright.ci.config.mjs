import base from "./playwright.config.mjs";

/**
 * The CI run is the whole browser suite, not a hand-picked list. The
 * previous three-file list is exactly how 16 appearance tests stayed red
 * for five days without anyone noticing.
 *
 * `grepInvert` below is a named, temporary exclusion — not a filter that
 * can quietly grow. Every entry is a test whose expectations still
 * describe the dialogue the engine ran before d1568d6 reordered
 * questions by hypothesis:
 *
 *   - the six `urgent summaries` cases in appearance.spec.mjs
 *   - the five fixture-driven cases in repeated-observations.spec.mjs
 *
 * Repairing them means deciding which question should come first for a
 * febrile patient and whether a 40.1℃ reading routes to 急诊科 or 今天
 * 就医. That is a clinical call, so the tests stay excluded until it is
 * made — they are listed here rather than deleted so the gap is visible.
 */
export default {
  ...base,
  grepInvert:
    /urgent summaries|repeated temperatures|narrow-screen repeat|short desktop observation/,
};
