// The pinned SDK deliberately removes NODE_* from its private Job runner's
// environment. Supply our trusted runtime preload as explicit argv for that
// exact entry only, retaining its IPC, credential scrub and native Job owner.
const { resolve } = require("node:path");

function adaptRunnerSpawn(original, { node, runner, preload }) {
  const identity = (path) => {
    const value = resolve(path);
    return process.platform === "win32" ? value.toLowerCase() : value;
  };
  const nodeId = identity(node),
    runnerId = identity(runner);
  return function (command, args, options) {
    if (
      typeof command === "string" &&
      identity(command) === nodeId &&
      Array.isArray(args) &&
      typeof args[0] === "string" &&
      identity(args[0]) === runnerId
    ) {
      args = [
        "--preserve-symlinks",
        "--preserve-symlinks-main",
        "--require",
        preload,
        ...args,
      ];
    }
    return Reflect.apply(original, this, [command, args, options]);
  };
}

module.exports = { adaptRunnerSpawn };
