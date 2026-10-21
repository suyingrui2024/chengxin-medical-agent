import { _electron as electron, test, expect } from "@playwright/test";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("explicit live mode configures a model without making an automatic model call", async () => {
  const profile = await mkdtemp(
    path.join(root, ".runtime", "desktop-test-live-"),
  );
  const environment = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: profile,
    MEDAGENT_DESKTOP_LIVE: "1",
    MEDAGENT_BACKEND: "compatible",
    MEDAGENT_LLM_BASE_URL: "https://fixture.invalid/v1",
    MEDAGENT_LLM_API_KEY: "desktop-test-only-secret",
    MEDAGENT_LLM_MODEL: "fixture-only",
  };
  delete environment.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  const desktop = await electron.launch({
    executablePath,
    args: executablePath ? [] : [root],
    env: environment,
  });
  try {
    const window = await desktop.firstWindow();
    await expect(window.getByText("本机服务已连接")).toBeVisible({
      timeout: 15000,
    });
    await expect(window.locator("#backend-pill")).toContainText("直接模型");
    const result = await window.evaluate(async () => {
      const settings = await (await fetch("/api/settings")).json();
      const created = await (
        await fetch("/api/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        })
      ).json();
      const trace = await (
        await fetch(`/api/sessions/${created.session_id}/events`)
      ).json();
      return {
        settings,
        modelCalls: trace.events.filter(
          (event) => event.event_type === "model.requested",
        ).length,
      };
    });
    expect(result.settings.backend).toBe("compatible");
    expect(result.settings.has_api_key).toBe(true);
    expect(result.modelCalls).toBe(0);
    expect(JSON.stringify(result)).not.toContain("desktop-test-only-secret");
  } finally {
    await desktop.close();
    await rm(profile, { recursive: true, force: true });
  }
});

test("patient model credentials persist only through native secure storage", async () => {
  const profile = await mkdtemp(
    path.join(root, ".runtime", "desktop-test-credentials-"),
  );
  const environment = {
    ...process.env,
    MEDAGENT_DESKTOP_PROFILE: profile,
    MEDAGENT_DESKTOP_LIVE: "0",
  };
  delete environment.ELECTRON_RUN_AS_NODE;
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  const launch = () =>
    electron.launch({
      executablePath,
      args: executablePath ? [] : [root],
      env: environment,
    });
  let desktop = await launch();
  try {
    let window = await desktop.firstWindow();
    await expect(window.getByText("本机服务已连接")).toBeVisible({
      timeout: 15000,
    });
    await window.getByRole("button", { name: "模型与运行配置" }).click();
    const status = await window.evaluate(() =>
      window.chengxinDesktop.modelCredentials.status(),
    );
    if (!status.available) {
      await expect(window.locator("#remember-model-control")).toBeHidden();
      return;
    }
    const secret = "desktop-secure-storage-test-only";
    await window
      .getByRole("button", { name: "使用 DeepSeek 推荐配置" })
      .click();
    await window.getByLabel("API 密钥").fill(secret);
    await window.locator("#model-data-consent-input").check();
    await window.locator("#remember-model").check();
    await window.getByRole("button", { name: "应用配置" }).click();
    await expect(window.locator("#settings-dialog")).not.toBeVisible();
    await desktop.close();
    desktop = null;

    const stored = await readFile(
      path.join(profile, "model-credentials.v1"),
      "utf8",
    );
    expect(stored).not.toContain(secret);

    desktop = await launch();
    window = await desktop.firstWindow();
    await expect(window.getByText("本机服务已连接")).toBeVisible({
      timeout: 15000,
    });
    await expect(window.locator("#patient-mode-label")).toHaveText("DSH 问诊");
    const settings = await window.evaluate(async () =>
      (await fetch("/api/settings")).json(),
    );
    expect(settings.has_api_key).toBe(true);
    expect(JSON.stringify(settings)).not.toContain(secret);

    await window.getByRole("button", { name: "模型与运行配置" }).click();
    await expect(window.locator("#forget-saved-model")).toBeVisible();
    window.once("dialog", (dialog) => dialog.accept());
    await window.locator("#forget-saved-model").click();
    await expect(window.locator("#patient-mode-label")).toHaveText("离线演示");
    const cleared = await window.evaluate(async () =>
      (await fetch("/api/settings")).json(),
    );
    expect(cleared.has_api_key).toBe(false);
    await expect(
      readFile(path.join(profile, "model-credentials.v1"), "utf8"),
    ).rejects.toThrow();
  } finally {
    if (desktop) await desktop.close();
    await rm(profile, { recursive: true, force: true });
  }
});

test("desktop app owns its window, private sidecar and shutdown lifecycle", async () => {
  const profile = await mkdtemp(path.join(root, ".runtime", "desktop-test-"));
  const environment = { ...process.env };
  delete environment.ELECTRON_RUN_AS_NODE;
  environment.MEDAGENT_DESKTOP_PROFILE = profile;
  environment.MEDAGENT_DESKTOP_LIVE = "0";
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  const desktop = await electron.launch({
    executablePath,
    args: executablePath ? [] : [root],
    env: environment,
  });
  let origin;
  try {
    const window = await desktop.firstWindow();
    await expect(
      window.getByRole("button", { name: "开始问诊", exact: true }),
    ).toBeVisible();
    origin = new URL(window.url()).origin;
    await expect(window.getByText("本机服务已连接")).toBeVisible();
    const preferences = await desktop.evaluate(({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows()[0];
      const prefs = win.webContents.getLastWebPreferences();
      return {
        title: win.getTitle(),
        sandbox: prefs.sandbox,
        nodeIntegration: prefs.nodeIntegration,
        contextIsolation: prefs.contextIsolation,
      };
    });
    expect(preferences).toEqual({
      title: "澄心问诊",
      sandbox: true,
      nodeIntegration: false,
      contextIsolation: true,
    });
    // Requests from ordinary browsers/HTTP clients cannot use this desktop instance.
    expect((await fetch(`${origin}/api/sessions`)).status).toBe(401);
    expect((await fetch(`${origin}/`)).status).toBe(401);
    expect(await window.evaluate(() => typeof window.require)).toBe(
      "undefined",
    );
    await mkdir(path.join(root, "docs/assets/ui"), { recursive: true });
    await window.screenshot({
      path: path.join(root, "docs/assets/ui/07-desktop-app.png"),
    });
    await window.getByRole("button", { name: "开始问诊", exact: true }).click();
    await expect(window.locator("#active-question .visual-choice")).toHaveCount(
      3,
    );
    await window.screenshot({
      path: path.join(root, "docs/assets/ui/21-desktop-visual-question.png"),
    });
    await window.getByRole("button", { name: "我自己", exact: true }).click();
    await window.getByRole("textbox", { name: "输入你的描述" }).fill("32岁");
    await window.getByRole("button", { name: "发送回答" }).click();
    await expect(
      window.locator(".fact-row").filter({ hasText: "年龄" }),
    ).toContainText("32 岁");
    await window.getByLabel("界面模式").selectOption("developer");
    await window.getByRole("button", { name: "模型与运行配置" }).click();
    await expect(
      window.getByRole("dialog", { name: "模型与运行配置" }),
    ).toBeVisible();
    await window.getByRole("button", { name: "测试连接" }).click();
    await expect(window.locator("#settings-feedback")).toContainText("已就绪");
    await window.getByRole("button", { name: "关闭配置" }).click();
    await window.locator('.nav-item[data-view="capabilities"]').click();
    await window.locator("#capability-select").selectOption("calculate.units");
    await window.getByRole("button", { name: "执行并查看公式" }).click();
    await expect(window.locator("#capability-test-result")).toContainText(
      "1250 g",
    );
    await window.locator('.nav-item[data-view="tasks"]').click();
    await window
      .getByRole("button", { name: "查看图示与步骤" })
      .first()
      .click();
    await expect(
      window.getByRole("dialog", { name: "图示任务预览" }),
    ).toBeVisible();
    await window.getByRole("button", { name: "下一步", exact: true }).click();
    await expect(window.locator("#task-preview .task-image")).toHaveAttribute(
      "src",
      "/neutral-temperature.svg#step-2",
    );
    await window.screenshot({
      path: path.join(root, "docs/assets/ui/11-desktop-structured.png"),
    });
    await window.getByRole("button", { name: "关闭预览" }).click();
    await window.locator('.nav-item[data-view="inspect"]').click();
    const exportPath = path.join(
      root,
      "test-results/desktop/synthetic-native-export.json",
    );
    await desktop.evaluate(({ BrowserWindow }, target) => {
      globalThis.medagentEvaluationDownload = "waiting";
      BrowserWindow.getAllWindows()[0].webContents.session.once(
        "will-download",
        (_event, item) => {
          item.setSavePath(target);
          item.once("done", (_doneEvent, state) => {
            globalThis.medagentEvaluationDownload = state;
          });
        },
      );
    }, exportPath);
    await window.getByRole("button", { name: "导出本次记录" }).click();
    await expect
      .poll(
        () => desktop.evaluate(() => globalThis.medagentEvaluationDownload),
        { timeout: 10000 },
      )
      .toBe("completed");
    const exported = JSON.parse(await readFile(exportPath, "utf8"));
    expect(exported.case.facts.age_years.value).toBe(32);
    expect(exported.events.length).toBeGreaterThan(0);
  } finally {
    await desktop.close();
  }
  await rm(profile, { recursive: true, force: true });
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
});
