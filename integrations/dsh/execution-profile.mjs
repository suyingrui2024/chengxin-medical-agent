import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

// DSH imports plugins from a synthetic directory URL below profiles/. Node's
// package-scope lookup starts at its parent, outside the sdk profile manifest.
// Give that parent an owned scope so an unreadable host ancestor cannot force
// the loader to bypass DSH's installed-package resolver inside containment.
export async function ensureExecutionProfileScope(state) {
  const profiles = join(state, "home", "profiles");
  await mkdir(profiles, { recursive: true });
  try {
    await writeFile(
      join(profiles, "package.json"),
      JSON.stringify({
        name: "chengxin-execution-profiles",
        private: true,
        type: "module",
      }) + "\n",
      { flag: "wx", mode: 0o600 },
    );
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  }
}
