import path from "node:path";

/**
 * Where a spec writes its acceptance screenshot.
 *
 * These used to be literal `docs/assets/ui/...` paths, so every full run
 * rewrote ~80 committed PNGs: the working tree was permanently dirty and a
 * real visual change was indistinguishable from rendering noise.
 *
 * Specs now name the file only, and the root is chosen here:
 *
 *   - normal runs write under the Playwright output directory, which is
 *     already gitignored and cleared per run;
 *   - `UPDATE_DOC_SHOTS=1` (`npm run shots`) writes into `docs/assets/ui`,
 *     which is the deliberate way to refresh the committed screenshots.
 */
export const DOC_SHOTS_DIR = "docs/assets/ui";
// Outside Playwright's outputDir on purpose: that directory is emptied at
// the start of every invocation, and `test:ui:ci` runs one invocation per
// spec file — shots kept there would survive only for the last file.
const OUTPUT_SHOTS_DIR = "test-results/shots";

/** Root directory for this run's screenshots. */
export const SHOTS_DIR = process.env.UPDATE_DOC_SHOTS
  ? DOC_SHOTS_DIR
  : OUTPUT_SHOTS_DIR;

/**
 * Resolve a screenshot name (e.g. `"78-wide-location-revision.png"`, or a
 * prefix a fixture appends `-review.png` to) against the root for this run.
 */
export function shotPath(name) {
  return path.posix.join(SHOTS_DIR, name);
}
