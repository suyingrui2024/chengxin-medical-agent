const fs = require("node:fs");
const path = require("node:path");

const FILE_NAME = "model-credentials.v1";
const FIELDS = ["backend", "base_url", "model", "api_key", "vision_model"];

function secureStorageAvailable(safeStorage, platform = process.platform) {
  if (!safeStorage?.isEncryptionAvailable()) return false;
  if (
    platform === "linux" &&
    typeof safeStorage.getSelectedStorageBackend === "function" &&
    safeStorage.getSelectedStorageBackend() === "basic_text"
  )
    return false;
  return true;
}

function normalize(value, { allowBlankKey = false } = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("模型配置格式无效");
  if (Object.keys(value).some((key) => !FIELDS.includes(key)))
    throw new Error("模型配置包含未知字段");
  const result = {};
  for (const field of FIELDS) {
    const item = value[field];
    if (typeof item !== "string" || item.length > 4096)
      throw new Error("模型配置字段无效");
    result[field] = item.trim();
  }
  if (!new Set(["compatible", "dsh"]).has(result.backend))
    throw new Error("只能保存在线模型配置");
  let endpoint;
  try {
    endpoint = new URL(result.base_url);
  } catch {
    throw new Error("模型服务地址无效");
  }
  if (
    endpoint.protocol !== "https:" ||
    endpoint.username ||
    endpoint.password ||
    endpoint.search ||
    endpoint.hash
  )
    throw new Error("保存的模型服务必须使用安全的 HTTPS 地址");
  if (!result.model || (!allowBlankKey && !result.api_key))
    throw new Error("保存模型配置需要模型 ID 和 API 密钥");
  return result;
}

function createModelCredentialStore(safeStorage, directory, platform) {
  const target = path.join(directory, FILE_NAME);

  function available() {
    return secureStorageAvailable(safeStorage, platform);
  }

  function read() {
    if (!available() || !fs.existsSync(target)) return null;
    try {
      const encrypted = Buffer.from(fs.readFileSync(target, "utf8"), "base64");
      return normalize(JSON.parse(safeStorage.decryptString(encrypted)));
    } catch {
      return null;
    }
  }

  function save(candidate) {
    if (!available()) throw new Error("当前系统没有可用的安全凭据存储");
    let config = normalize(candidate, { allowBlankKey: true });
    if (!config.api_key) {
      const existing = read();
      if (!existing) throw new Error("请重新填写 API 密钥后再保存到本机");
      config = { ...config, api_key: existing.api_key };
    }
    config = normalize(config);
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    const temporary = `${target}.${process.pid}.tmp`;
    const encrypted = safeStorage.encryptString(JSON.stringify(config));
    fs.writeFileSync(temporary, encrypted.toString("base64"), { mode: 0o600 });
    fs.renameSync(temporary, target);
    return { saved: true };
  }

  function clear() {
    fs.rmSync(target, { force: true });
    return { cleared: true };
  }

  function status() {
    return { available: available(), has_saved_config: read() !== null };
  }

  return { read, save, clear, status, target };
}

module.exports = {
  createModelCredentialStore,
  secureStorageAvailable,
};
