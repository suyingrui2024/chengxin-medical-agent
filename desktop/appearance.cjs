const fs = require("node:fs");
const path = require("node:path");

const values = ["forest", "ocean", "iris", "sand", "slate", "night", "system"];
const valid = (value) => typeof value === "string" && values.includes(value);

function readAppearance(directory) {
  try {
    const data = JSON.parse(
      fs.readFileSync(path.join(directory, "appearance.json"), "utf8"),
    );
    return data.schema === 1 && valid(data.value) ? data.value : "forest";
  } catch {
    return "forest";
  }
}

function saveAppearance(directory, value) {
  if (!valid(value)) throw new Error("无效的外观选项");
  fs.mkdirSync(directory, { recursive: true });
  const target = path.join(directory, "appearance.json");
  const temporary = `${target}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify({ schema: 1, value }) + "\n", {
    mode: 0o600,
  });
  fs.renameSync(temporary, target);
}

// Builds up to 2026-10 stored userData under appData/chengxin-medagent-<version>,
// so every upgrade silently reset the saved appearance and left the old
// directory behind forever. Only appearance.json is carried over: credentials
// and patient data never lived in the versioned directories (both already use
// unversioned locations), and the legacy directories are deliberately left in
// place rather than deleted. Best-effort: any failure leaves the default
// appearance and must never block startup.
function migrateLegacyAppearance(appDataDirectory, userDataDirectory) {
  try {
    const target = path.join(userDataDirectory, "appearance.json");
    if (fs.existsSync(target)) return false;
    let newest = null;
    for (const entry of fs.readdirSync(appDataDirectory, {
      withFileTypes: true,
    })) {
      if (!entry.isDirectory() || !/^chengxin-medagent-.+/.test(entry.name))
        continue;
      const candidate = path.join(
        appDataDirectory,
        entry.name,
        "appearance.json",
      );
      try {
        const { mtimeMs } = fs.statSync(candidate);
        if (!newest || mtimeMs > newest.mtimeMs)
          newest = { candidate, mtimeMs };
      } catch {}
    }
    if (!newest) return false;
    fs.mkdirSync(userDataDirectory, { recursive: true });
    fs.copyFileSync(newest.candidate, target);
    return true;
  } catch {
    return false;
  }
}

module.exports = {
  readAppearance,
  saveAppearance,
  valid,
  migrateLegacyAppearance,
};
