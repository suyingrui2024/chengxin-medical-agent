// Shared bounded path contract, without importing any workspace code.
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
  if (value.includes(".")) return ["."];
  return [...new Set(value)]
    .sort()
    .filter(
      (path, _, paths) =>
        !paths.some(
          (parent) => path !== parent && path.startsWith(parent + "/"),
        ),
    );
}
export function mergeCheckpointPaths(previous, current) {
  if (!previous?.length) return checkpointPaths(current);
  if (previous.includes(".") || current.includes(".")) return ["."];
  const paths = [...new Set([...previous, ...current])]
    .sort()
    .filter(
      (path, _, all) =>
        !all.some((parent) => path !== parent && path.startsWith(parent + "/")),
    );
  return checkpointPaths(paths);
}
export function coversCheckpointPaths(actual, required) {
  return required.every((path) =>
    actual.some(
      (parent) =>
        parent === "." || path === parent || path.startsWith(parent + "/"),
    ),
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
