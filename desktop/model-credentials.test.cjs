const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  createModelCredentialStore,
  secureStorageAvailable,
} = require("./model-credentials.cjs");

function fakeSafeStorage() {
  return {
    isEncryptionAvailable: () => true,
    getSelectedStorageBackend: () => "keyring",
    encryptString: (value) => Buffer.from(`encrypted:${value}`, "utf8"),
    decryptString: (value) => value.toString("utf8").replace(/^encrypted:/, ""),
  };
}

const config = {
  backend: "dsh",
  base_url: "https://api.deepseek.com/anthropic",
  model: "deepseek-flash",
  api_key: "test-only-secret",
  vision_model: "deepseek-flash",
};

test("credentials are encrypted, never exposed by status, and can be cleared", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "chengxin-key-"));
  try {
    const store = createModelCredentialStore(
      fakeSafeStorage(),
      directory,
      "darwin",
    );
    assert.deepEqual(store.status(), {
      available: true,
      has_saved_config: false,
    });
    store.save(config);
    const disk = fs.readFileSync(store.target, "utf8");
    assert.equal(disk.includes(config.api_key), false);
    assert.deepEqual(store.read(), config);
    assert.deepEqual(store.status(), {
      available: true,
      has_saved_config: true,
    });
    assert.equal(
      JSON.stringify(store.status()).includes(config.api_key),
      false,
    );
    store.clear();
    assert.equal(store.read(), null);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("blank key reuses only an existing encrypted key", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "chengxin-key-"));
  try {
    const store = createModelCredentialStore(
      fakeSafeStorage(),
      directory,
      "win32",
    );
    assert.throws(() => store.save({ ...config, api_key: "" }), /重新填写/);
    store.save(config);
    store.save({ ...config, model: "new-model", api_key: "" });
    assert.equal(store.read().api_key, config.api_key);
    assert.equal(store.read().model, "new-model");
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("Linux basic_text fallback is not accepted as secure storage", () => {
  const storage = fakeSafeStorage();
  storage.getSelectedStorageBackend = () => "basic_text";
  assert.equal(secureStorageAvailable(storage, "linux"), false);
});
