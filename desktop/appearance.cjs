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

module.exports = { readAppearance, saveAppearance, valid };
