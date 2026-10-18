// Shared bounded path contract, without importing any workspace code.
const IGNORED = new Set([
  ".git",
  "node_modules",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
]);
export function checkpointPathCovers(parent, path) {
  if (parent === path) return true;
  const suffix =
    parent === "."
      ? path
      : path.startsWith(parent + "/")
        ? path.slice(parent.length + 1)
        : null;
  return (
    suffix !== null && !suffix.split("/").some((part) => IGNORED.has(part))
  );
}
const ordered = (paths) =>
  [...new Set(paths)].sort((a, b) =>
    Buffer.compare(Buffer.from(a), Buffer.from(b)),
  );
const minimal = (paths) =>
  ordered(paths).filter(
    (path, _, all) =>
      !all.some(
        (parent) => path !== parent && checkpointPathCovers(parent, path),
      ),
  );
export function checkpointPaths(value = ["."]) {
  if (!Array.isArray(value) || !value.length || value.length > 16)
    throw new Error("检查依赖路径须为1至16项");
  for (const path of value)
    if (
      typeof path !== "string" ||
      !path.isWellFormed() ||
      !path ||
      Buffer.byteLength(path) > 1024 ||
      /[\\\x00-\x1f]/.test(path) ||
      (path !== "." &&
        path.split("/").some((part) => ["", ".", ".."].includes(part)))
    )
      throw new Error("检查依赖路径无效");
  return minimal(value);
}
export function mergeCheckpointPaths(previous, current) {
  if (!previous?.length) return checkpointPaths(current);
  const paths = minimal([...previous, ...current]);
  return checkpointPaths(paths);
}
export function coversCheckpointPaths(actual, required) {
  return required.every((path) =>
    actual.some((parent) => checkpointPathCovers(parent, path)),
  );
}
export function validInputScope(value) {
  if (!Array.isArray(value) || value.length > 16) return false;
  if (!value.length) return true;
  try {
    return JSON.stringify(checkpointPaths(value)) === JSON.stringify(value);
  } catch {
    return false;
  }
}
export function coversVerifiedInputs(verified, required = []) {
  return (
    !required.length ||
    (validInputScope(verified) && coversCheckpointPaths(verified, required))
  );
}
export function validCheckpoint(value) {
  if (
    !value ||
    value.schema !== "workspace-checkpoint/v1" ||
    typeof value.complete !== "boolean" ||
    !Number.isSafeInteger(value.files) ||
    value.files < 0 ||
    value.files > 20000 ||
    value.symlink_targets_followed !== false ||
    !Array.isArray(value.ignored_directories) ||
    value.ignored_directories.length > 16 ||
    value.ignored_directories.some(
      (name) => typeof name !== "string" || name.length > 80,
    )
  )
    return false;
  try {
    if (
      JSON.stringify(checkpointPaths(value.paths)) !==
      JSON.stringify(value.paths)
    )
      return false;
  } catch {
    return false;
  }
  return value.complete
    ? typeof value.sha256 === "string" && /^[0-9a-f]{64}$/.test(value.sha256)
    : value.sha256 === null;
}
