// Preserve Node's realpath contract through the existing authenticated host
// gateway when the Windows containment API cannot query a volume's path name.
// This returns metadata only; opening/reading/writing still uses the OS sandbox.
const fs = require("node:fs");
const { resolve } = require("node:path");
const { fileURLToPath } = require("node:url");
const { Worker } = require("node:worker_threads");
const { syncBuiltinESMExports } = require("node:module");
const endpoint = process.env.CHENGXIN_PATH_BROKER;
// AppContainer launch may replace TEMP with an uncreated profile directory.
// Restore the already-created, explicitly writable per-task temp directory.
if (process.env.CHENGXIN_TEMP) {
  process.env.TEMP = process.env.CHENGXIN_TEMP;
  process.env.TMP = process.env.CHENGXIN_TEMP;
}

if (endpoint && !process.env.CHENGXIN_PATH_BROKER_WORKER) {
  const url = new URL(endpoint);
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    url.username ||
    url.password
  )
    throw new Error("Invalid native path metadata gateway");
  const workerSource = `
    const { parentPort, workerData } = require('node:worker_threads');
    const http = require('node:http');
    const header = new Int32Array(workerData.shared, 0, 2);
    const output = new Uint8Array(workerData.shared, 8);
    parentPort.on('message', path => {
      let finished = false;
      const finish = result => {
        if (finished) return; finished = true;
        let data = Buffer.from(JSON.stringify(result));
        if (data.length > output.length) data = Buffer.from('{"ok":false,"code":"EIO"}');
        output.set(data); Atomics.store(header, 1, data.length);
        Atomics.store(header, 0, 1); Atomics.notify(header, 0);
      };
      const request = http.request(workerData.endpoint, {
        method: 'POST', agent: false,
        headers: {'x-api-key': workerData.token, 'content-type': 'application/json'}
      }, response => {
        let body = '';
        response.setEncoding('utf8');
        response.on('data', chunk => { body += chunk; if (body.length > 131072) request.destroy(new Error('Response too large')); });
        response.on('error', () => finish({ok:false,code:'EIO'}));
        response.on('end', () => {
          try { finish(response.statusCode === 200 ? JSON.parse(body) : {ok:false,code:'EACCES'}); }
          catch { finish({ok:false,code:'EIO'}); }
        });
      });
      request.setTimeout(2500, () => request.destroy(new Error('Metadata timeout')));
      request.on('error', () => finish({ok:false,code:'EIO'}));
      request.end(JSON.stringify({path}));
    });`;
  let bridge;
  function startBridge() {
    const shared = new SharedArrayBuffer(262152);
    const worker = new Worker(workerSource, {
      eval: true,
      execArgv: [],
      env: { ...process.env, CHENGXIN_PATH_BROKER_WORKER: "1" },
      workerData: { shared, endpoint, token: process.env.DEEPSEEK_API_KEY },
    });
    worker.on("error", () => {});
    worker.unref();
    return {
      worker,
      header: new Int32Array(shared, 0, 2),
      output: new Uint8Array(shared, 8),
    };
  }
  function canonical(path, options) {
    const input = resolve(
      path instanceof URL
        ? fileURLToPath(path)
        : Buffer.isBuffer(path)
          ? path.toString()
          : path,
    );
    bridge ||= startBridge();
    Atomics.store(bridge.header, 0, 0);
    bridge.worker.postMessage(input);
    const completed = Atomics.wait(bridge.header, 0, 0, 3500);
    if (completed === "timed-out") {
      bridge.worker.terminate();
      bridge = undefined;
      throw Object.assign(new Error("Native path metadata timed out"), {
        code: "ETIMEDOUT",
        syscall: "realpath",
        path: input,
      });
    }
    const result = JSON.parse(
      Buffer.from(
        bridge.output.subarray(0, Atomics.load(bridge.header, 1)),
      ).toString(),
    );
    if (!result.ok || typeof result.path !== "string")
      throw Object.assign(
        new Error(`${result.code || "EIO"}: realpath '${input}'`),
        { code: result.code || "EIO", syscall: "realpath", path: input },
      );
    const encoding =
      typeof options === "string" ? options : options?.encoding || "utf8";
    const value = Buffer.from(result.path);
    return encoding === "buffer" ? value : value.toString(encoding);
  }
  function asynchronous(path, options, callback) {
    if (typeof options === "function") {
      callback = options;
      options = undefined;
    }
    if (typeof callback !== "function")
      throw new TypeError("A realpath callback is required");
    queueMicrotask(() => {
      let result;
      try {
        result = canonical(path, options);
      } catch (error) {
        callback(error);
        return;
      }
      callback(null, result);
    });
  }
  canonical.native = canonical;
  asynchronous.native = asynchronous;
  fs.realpathSync = canonical;
  fs.realpath = asynchronous;
  fs.promises.realpath = async (path, options) => canonical(path, options);
  syncBuiltinESMExports();
}
