import { _electron as electron, test, expect } from "@playwright/test";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("appearance survives desktop restarts and a different private service origin", async () => {
  await mkdir(path.join(root, ".runtime"), { recursive: true });
  const profile = await mkdtemp(
    path.join(root, ".runtime", "appearance-native-"),
  );
  const env = { ...process.env, MEDAGENT_DESKTOP_PROFILE: profile };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.MEDAGENT_DESKTOP_LIVE;
  const launch = () => electron.launch({ args: [root], env });
  let desktop;
  try {
    desktop = await launch();
    const window = await desktop.firstWindow();
    await expect(window.getByText("本机服务已连接")).toBeVisible({
      timeout: 15000,
    });
    await window
      .getByRole("button", { name: "外观与皮肤", exact: true })
      .click();
    await window.locator('input[name="appearance"][value="night"]').check();
    await expect(window.locator("#appearance-status")).toContainText(
      "下次打开会保留",
    );
    expect(
      JSON.parse(await readFile(path.join(profile, "appearance.json"), "utf8")),
    ).toEqual({ schema: 1, value: "night" });
    expect(
      await window.evaluate(async () => {
        try {
          await window.chengxinDesktop.appearance.save("../../invalid");
          return false;
        } catch {
          return true;
        }
      }),
    ).toBe(true);
    await desktop.close();
    desktop = await launch();
    const reopened = await desktop.firstWindow();
    await expect(reopened.getByText("本机服务已连接")).toBeVisible({
      timeout: 15000,
    });
    await expect(reopened.locator("html")).toHaveAttribute(
      "data-theme",
      "night",
    );
    await reopened
      .getByRole("button", { name: "外观与皮肤", exact: true })
      .click();
    await expect(reopened.locator('input[value="night"]')).toBeChecked();
    const saved = JSON.parse(
      await readFile(path.join(profile, "appearance.json"), "utf8"),
    );
    expect(saved).toEqual({ schema: 1, value: "night" });
  } finally {
    await desktop?.close();
    await rm(profile, { recursive: true, force: true });
  }
});
