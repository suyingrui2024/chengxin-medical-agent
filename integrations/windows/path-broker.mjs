// Canonical-path metadata only. Authorize both the input and resolved target;
// the process container remains responsible for all actual file operations.
import { realpathSync, statSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";

function within(path, root) {
  const suffix = relative(root, path);
  return (
    !suffix ||
    (!isAbsolute(suffix) && suffix !== ".." && !suffix.startsWith(".." + sep))
  );
}

export function createPathResolver(roots) {
  const allowed = roots.map((path) => {
    const recursive = typeof path === "string" || path.recursive !== false;
    path = typeof path === "string" ? path : path.path;
    const canonical = realpathSync.native(path);
    return {
      input: resolve(path),
      canonical,
      inputParent: dirname(resolve(path)),
      canonicalParent: realpathSync.native(dirname(resolve(path))),
      directory: recursive && statSync(canonical).isDirectory(),
    };
  });
  return (path) => {
    if (
      typeof path !== "string" ||
      path.length > 32768 ||
      path.includes("\0") ||
      !isAbsolute(path)
    )
      return { ok: false, code: "EINVAL" };
    const input = resolve(path);
    const candidates = allowed.filter((root) => {
      // Windows TEMP and user paths may use a known 8.3 parent spelling.
      // Normalize that already-authorized parent's spelling without querying
      // the requested target before containment has been established.
      const spellings = [input];
      if (within(input, root.inputParent))
        spellings.push(
          resolve(root.canonicalParent, relative(root.inputParent, input)),
        );
      return spellings.some((candidate) =>
        root.directory
          ? within(candidate, root.input) || within(candidate, root.canonical)
          : !relative(root.input, candidate) ||
            !relative(root.canonical, candidate),
      );
    });
    if (!candidates.length) return { ok: false, code: "EACCES" };
    try {
      const canonical = realpathSync.native(input);
      if (
        !candidates.some((root) =>
          root.directory
            ? within(canonical, root.canonical)
            : !relative(root.canonical, canonical),
        )
      )
        return { ok: false, code: "EACCES" };
      return { ok: true, path: canonical };
    } catch (error) {
      return {
        ok: false,
        code: [
          "ENOENT",
          "ENOTDIR",
          "EACCES",
          "EPERM",
          "ELOOP",
          "ENAMETOOLONG",
        ].includes(error.code)
          ? error.code
          : "EIO",
      };
    }
  };
}
