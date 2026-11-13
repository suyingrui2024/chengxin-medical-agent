import path from "node:path";
import { fileURLToPath } from "node:url";

export const PROJECT_ROOT = fileURLToPath(new URL("../../", import.meta.url));
export const fromRoot = (...parts) => path.resolve(PROJECT_ROOT, ...parts);
