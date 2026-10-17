import {
  _electron as electron,
  expect,
} from "/test-tools/node_modules/@playwright/test/index.mjs";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { readdir, readFile, writeFile } from "node:fs/promises";

const report = {
  synthetic_only: true,
  checks: {},
  scope:
    "Same-host empty HOME, readonly package, system Python, no development source mount or external network. No new-machine or clinical validation.",
};
let app;
let origin;
try {
  expect(await readdir("/home/clean")).toEqual([]);
  expect(existsSync("/home/suyingrui")).toBe(false);
  expect(existsSync("/app/resources/app/.env")).toBe(false);
  expect(existsSync("/app/resources/app/.venv")).toBe(false);
  report.checks.empty_user_environment = true;
  expect(
    Object.keys(process.env).filter((name) =>
      /API_KEY|TOKEN|PASSWORD|SECRET/.test(name),
    ),
  ).toEqual([]);
  const appMount = (await readFile("/proc/self/mountinfo", "utf8"))
    .split("\n")
    .map((line) => line.split(" "))
    .find((fields) => fields[4] === "/app");
  expect(appMount[5].split(",")).toContain("ro");
  report.checks.packaged_application_mount_readonly = true;
  report.checks.host_credentials_not_inherited = true;
  const python = JSON.parse(
    execFileSync(
      "/usr/bin/python3",
      [
        "-c",
        `
import json, os, socket, sys
sys.path.insert(0, '/app/resources/app/src')
import medagent
sock = socket.socket()
sock.settimeout(1)
network = sock.connect_ex(('1.1.1.1', 443))
sock.close()
print(json.dumps({'executable': sys.executable, 'version': sys.version.split()[0],
 'module': medagent.__file__, 'sys_path': sys.path, 'external_connect_errno': network,
 'interfaces': [name for _, name in socket.if_nameindex()]}))
`,
      ],
      { encoding: "utf8" },
    ),
  );
  expect(python.executable).toBe("/usr/bin/python3");
  expect(python.module).toBe("/app/resources/app/src/medagent/__init__.py");
  expect(
    python.sys_path.some((p) => /anaconda|medical-agent|\.venv/.test(p)),
  ).toBe(false);
  expect(python.interfaces).toEqual(["lo"]);
  expect(python.external_connect_errno).not.toBe(0);
  report.runtime = python;
  report.node = process.version;
  report.checks.only_packaged_python_source = true;
  report.checks.external_network_disconnected = true;
  app = await electron.launch({
    executablePath: "/app/Chengxin",
    args: [],
    env: { ...process.env },
  });
  const page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await expect(
    page.getByRole("button", { name: "开始问诊", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("本机服务已连接")).toBeVisible();
  origin = new URL(page.url()).origin;
  report.application = await app.evaluate(({ app, BrowserWindow }) => {
    const preferences =
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();
    return {
      version: app.getVersion(),
      path: app.getAppPath(),
      profile: app.getPath("userData"),
      argv: process.argv,
      preferences: {
        sandbox: preferences.sandbox,
        nodeIntegration: preferences.nodeIntegration,
        contextIsolation: preferences.contextIsolation,
      },
    };
  });
  const metadata = JSON.parse(
    await readFile("/app/resources/app/package.json", "utf8"),
  );
  expect(report.application.version).toBe(metadata.version);
  expect(report.application.path).toBe("/app/resources/app");
  expect(report.application.profile).toContain("/home/clean/");
  expect(report.application.preferences).toEqual({
    sandbox: true,
    nodeIntegration: false,
    contextIsolation: true,
  });
  expect((await fetch(`${origin}/api/sessions`)).status).toBe(401);
  report.checks.packaged_native_start = true;
  report.checks.private_service_rejects_unauthenticated_access = true;
  await page.screenshot({ path: "/out/welcome.png" });
  let last;
  async function perform(action) {
    const response = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        /\/api\/sessions(?:\/[^/]+\/messages)?$/.test(
          new URL(r.url()).pathname,
        ),
    );
    await action();
    const result = await response;
    expect(result.ok()).toBe(true);
    last = await result.json();
    await expect(page.locator("#busy-indicator")).toBeHidden();
  }
  async function answer(text) {
    await page.getByRole("textbox", { name: "输入你的描述" }).fill(text);
    await perform(() => page.getByRole("button", { name: "发送回答" }).click());
  }
  await perform(() =>
    page.getByRole("button", { name: "开始问诊", exact: true }).click(),
  );
  const sessionId =
    last.session_id || last.case?.session_id || last.state?.session_id;
  expect(typeof sessionId).toBe("string");
  await perform(() =>
    page.getByRole("button", { name: "我自己", exact: true }).click(),
  );
  for (const text of [
    "32岁",
    "咳嗽发烧两天",
    "都没有",
    "两天前逐渐出现",
    "变化不大",
    "5分",
    "喉咙痛",
    "没有",
    "干咳，没有咳血",
    "能正常喝水",
  ])
    await answer(text);
  await expect(
    page.getByRole("heading", { name: "测一次腋下体温" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(page.locator("#active-task .task-image")).toHaveAttribute(
    "src",
    "/temperature.svg#step-2",
  );
  expect(
    (
      await page.locator("#active-task .task-image").evaluate((img) => ({
        complete: img.complete,
        width: img.naturalWidth,
      }))
    ).width,
  ).toBeGreaterThan(0);
  await page.screenshot({ path: "/out/temperature.png" });
  await answer("380");
  await expect(page.locator("#active-task .task-feedback")).toBeVisible();
  const invalid = await page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}`)).json(),
    sessionId,
  );
  expect((invalid.case || invalid.state).facts.temperature_c).toBeUndefined();
  await answer("38.2℃");
  for (const text of ["没有", "不知道", "没有", "想整理信息去就医"])
    await answer(text);
  await expect(
    page.getByRole("heading", { name: "本次问诊摘要" }).last(),
  ).toBeVisible();
  await expect(
    page.locator(".summary-card").last().locator(".unknown-items"),
  ).toContainText("过敏信息");
  await page.screenshot({ path: "/out/summary.png" });
  await perform(() =>
    page.getByRole("button", { name: "摘要准确", exact: true }).click(),
  );
  await expect(page.locator("#composer")).toBeHidden();
  const snapshot = await page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}`)).json(),
    sessionId,
  );
  const trace = await page.evaluate(
    async (id) => (await fetch(`/api/sessions/${id}/events`)).json(),
    sessionId,
  );
  const state = snapshot.case || snapshot.state;
  expect(state.phase).toBe("closed");
  expect(state.facts.temperature_c.value).toBe(38.2);
  expect(state.facts.temperature_c.source).toBe("patient");
  expect(state.facts.temperature_c.quality).toBe("not_assessed");
  expect(state.facts.allergies.assertion).toBe("unknown");
  expect(
    trace.events.filter((e) => e.event_type === "model.requested"),
  ).toHaveLength(0);
  expect(errors).toEqual([]);
  report.checks.complete_demo_interview = true;
  report.checks.packaged_image_loaded = true;
  report.checks.invalid_reading_rejected = true;
  report.checks.unknown_and_measurement_provenance_preserved = true;
  report.checks.no_model_requests = true;
  report.checks.no_renderer_errors = true;
  await writeFile(
    "/out/synthetic-case.json",
    JSON.stringify({ snapshot, trace }, null, 2) + "\n",
  );
  await app.close();
  app = undefined;
  await expect
    .poll(async () => {
      try {
        await fetch(`${origin}/api/health`);
        return false;
      } catch {
        return true;
      }
    })
    .toBe(true);
  report.checks.private_service_stopped = true;
  report.passed = Object.values(report.checks).every(Boolean);
} catch (error) {
  report.passed = false;
  report.error = String(error.stack || error);
  if (app) {
    try {
      await (await app.firstWindow()).screenshot({ path: "/out/failure.png" });
    } catch {
      /* Keep the original failure. */
    }
  }
} finally {
  await app?.close();
  await writeFile("/out/probe.json", JSON.stringify(report, null, 2) + "\n");
}
console.log(
  JSON.stringify({
    passed: report.passed,
    checks: report.checks,
    error: report.error,
  }),
);
process.exitCode = report.passed ? 0 : 1;
