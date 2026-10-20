import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { reachSelfCheck } from "../fixtures/self-check-drafts.mjs";
import { degreeSnapshot } from "../fixtures/visual-answer-drafts.mjs";
import {
  createRepeatedObservationModel,
  verifyRepeatedObservations,
} from "../fixtures/repeated-observations.mjs";

const themes = ["forest", "ocean", "iris", "sand", "slate", "night"];
const axeSource = await readFile(
  new URL("../../node_modules/axe-core/axe.min.js", import.meta.url),
  "utf8",
);

async function contrast(page) {
  if (!(await page.evaluate(() => Boolean(window.axe)))) {
    await page.route("**/appearance-audit.js", (route) =>
      route.fulfill({ contentType: "application/javascript", body: axeSource }),
    );
    await page.addScriptTag({ url: "/appearance-audit.js" });
  }
  const result = await page.evaluate(async () => {
    const report = await window.axe.run({
      runOnly: {
        type: "rule",
        values: ["color-contrast", "button-name", "label", "aria-dialog-name"],
      },
    });
    return report.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => ({
        target: n.target,
        reason: n.failureSummary,
      })),
    }));
  });
  expect(result).toEqual([]);
}

async function choose(page, theme) {
  const trigger = page.getByRole("button", { name: "外观与皮肤", exact: true });
  const returnToTask = !(await trigger.isVisible());
  if (returnToTask)
    await page.getByRole("button", { name: "展开对话记录" }).click();
  await trigger.click();
  await page.locator(`input[name="appearance"][value="${theme}"]`).check();
  await page.getByRole("button", { name: "关闭外观设置" }).click();
  if (returnToTask)
    await page.getByRole("button", { name: "返回当前任务" }).click();
}

async function controlContrast(page) {
  const contrasts = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    const luminance = (name) => {
      const hex = style.getPropertyValue(name).trim();
      const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
      return rgb
        .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
        .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
    };
    return ["--surface", "--raised", "--selected"].map((background) => {
      const [low, high] = [
        luminance("--line-strong"),
        luminance(background),
      ].sort((a, b) => a - b);
      return { background, ratio: (high + 0.05) / (low + 0.05) };
    });
  });
  for (const item of contrasts)
    expect(
      item.ratio,
      `Input boundary against ${item.background}`,
    ).toBeGreaterThanOrEqual(3);
}

test.beforeEach(async ({ request }) => {
  await request.post("/api/settings", { data: { backend: "demo" } });
});

for (const theme of themes) {
  test(`${theme}: all surfaces and live drafts keep contrast and state`, async ({
    page,
  }, info) => {
    await page.goto("/");
    await expect(page.getByText("本机服务已连接")).toBeVisible();
    await choose(page, theme);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await controlContrast(page);
    await contrast(page);
    await page.screenshot({
      path: info.outputPath(`${theme}-welcome.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "外观与皮肤", exact: true }).click();
    await contrast(page);
    await page.screenshot({ path: info.outputPath(`${theme}-chooser.png`) });
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("button", { name: "外观与皮肤", exact: true }),
    ).toBeFocused();

    const id = await reachSelfCheck(page);
    await page.getByRole("button", { name: "背/腰部", exact: true }).click();
    const input = page.getByRole("textbox", { name: "输入你的描述" });
    await input.fill("这是未发送的补充。");
    const before = await degreeSnapshot(page, id);
    await choose(page, theme === "forest" ? "night" : "forest");
    await choose(page, theme);
    expect(await degreeSnapshot(page, id)).toEqual(before);
    await expect(input).toHaveValue("这是未发送的补充。");
    await expect(
      page.locator('.body-pin[data-location="back"]'),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".body-back")).toBeVisible();
    await expect(page.locator(".body-front")).toBeHidden();
    await expect(page.locator(".body-right-label")).toHaveText("左");
    await expect(
      page.locator(".body-selection-marker .body-region"),
    ).toHaveCount(1);
    await contrast(page);
    await page.screenshot({
      path: info.outputPath(`${theme}-location.png`),
      fullPage: true,
    });

    await page.getByRole("button", { name: "展开对话记录" }).click();
    await page.getByLabel("界面模式").selectOption("developer");
    for (const view of [
      "history",
      "tasks",
      "inspect",
      "capabilities",
      "execution",
    ]) {
      await page.locator(`.nav-item[data-view="${view}"]`).click();
      await contrast(page);
    }
    await page.getByRole("button", { name: "模型与运行配置" }).click();
    await contrast(page);
  });
}

for (const theme of themes) {
  test(`${theme}: urgent summaries retain their meaning and readable measurements`, async ({
    page,
    request,
  }) => {
    const model = await createRepeatedObservationModel();
    try {
      await request.post("/api/settings", {
        data: {
          backend: "compatible",
          base_url: model.endpoint,
          model: "fixture",
          api_key: "local-appearance-test",
        },
      });
      await page.goto("/");
      await choose(page, theme);
      await verifyRepeatedObservations(page, model);
      await contrast(page);
    } finally {
      await request.post("/api/settings", { data: { backend: "demo" } });
      await model.close();
    }
  });
}

for (const [width, height] of [
  [390, 844],
  [320, 640],
  [900, 648],
]) {
  test(`appearance picker works at ${width}×${height}, with keyboard and reload`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await choose(page, "ocean");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "ocean");
    const trigger = page.getByRole("button", {
      name: "外观与皮肤",
      exact: true,
    });
    await trigger.click();
    await expect(page.locator('input[value="ocean"]')).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "iris");
    for (const theme of themes) {
      await page.locator(`input[name="appearance"][value="${theme}"]`).check();
      await contrast(page);
    }
    const overflow = await page.evaluate(() => {
      const d = document.querySelector("#appearance-dialog");
      return {
        page: document.documentElement.scrollWidth > innerWidth,
        dialog: d.scrollWidth > d.clientWidth,
      };
    });
    expect(overflow).toEqual({ page: false, dialog: false });
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
  });
}

test("system mode responds to OS changes, manual mode stays chosen, tabs sync", async ({
  page,
  context,
}) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await choose(page, "system");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "forest");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
  await choose(page, "sand");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "sand");
  const other = await context.newPage();
  await other.goto("/");
  await expect(other.locator("html")).toHaveAttribute("data-theme", "sand");
  await choose(other, "slate");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "slate");
  await other.close();
});

test("invalid or unavailable storage keeps appearance controls usable", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("chengxin.appearance.v1", "not-a-theme"),
  );
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "forest");
  await page.evaluate(() => {
    Storage.prototype.setItem = () => {
      throw new Error("storage unavailable");
    };
  });
  await choose(page, "night");
  await page.getByRole("button", { name: "外观与皮肤", exact: true }).click();
  await expect(page.locator("#appearance-status")).toContainText(
    "暂时无法保存",
  );
  await expect(page.locator("html")).toHaveAttribute("data-theme", "night");
});

test("enlarging each temperature step preserves the draft and progress", async ({
  page,
}, info) => {
  await page.goto("/");
  const id = await reachSelfCheck(page, "发热");
  const input = page.getByRole("textbox", { name: "输入你的描述" });
  // The compact guide exposes free text after the preparation and placement
  // steps.  Create the draft through that patient-visible path, then return
  // to step 1 so each illustration is still audited below.
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(input).toBeVisible();
  await input.fill("尚未发送的读数补充");
  await page.getByRole("button", { name: "上一步", exact: true }).click();
  await page.getByRole("button", { name: "上一步", exact: true }).click();
  const before = await degreeSnapshot(page, id);
  for (const theme of ["forest", "night"]) {
    await choose(page, theme);
    for (let step = 1; step <= 3; step++) {
      await page.locator("#active-task .illustration-zoom").click();
      const dialog = page.getByRole("dialog", {
        name: `测一次腋下体温 · 步骤 ${step}`,
      });
      await expect(dialog).toBeVisible();
      await expect(dialog.locator("img")).toHaveAttribute(
        "src",
        new RegExp(`neutral-temperature.svg#step-${step}$`),
      );
      await contrast(page);
      await page.screenshot({
        path: info.outputPath(`${theme}-temperature-step-${step}.png`),
      });
      await page.keyboard.press("Escape");
      await expect(
        page.locator("#active-task .illustration-zoom"),
      ).toBeFocused();
      if (step < 3)
        await page.getByRole("button", { name: "下一步", exact: true }).click();
    }
    await contrast(page);
    await page.getByRole("button", { name: "上一步", exact: true }).click();
    await page.getByRole("button", { name: "上一步", exact: true }).click();
  }
  await expect(page.locator("#message-input")).toHaveValue(
    "尚未发送的读数补充",
  );
  expect(await degreeSnapshot(page, id)).toEqual(before);
});

test("missing instructional artwork keeps the written steps available", async ({
  page,
}) => {
  await page.route("**/neutral-temperature.svg", (route) => route.abort());
  await page.goto("/");
  await reachSelfCheck(page, "发热");
  await expect(page.locator("#active-task figcaption")).toContainText(
    "图示暂时无法加载",
  );
  await expect(page.locator("#active-task .step-text")).toContainText(
    "擦干腋窝",
  );
  await expect(page.locator("#active-task .illustration-zoom")).toBeHidden();
});

test("short developer windows can reach all navigation and model settings", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 648 });
  await page.goto("/");
  await page.getByLabel("界面模式").selectOption("developer");
  await page
    .getByRole("button", { name: "模型与运行配置", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "模型与运行配置" }),
  ).toBeVisible();
});

test("transient status messages remain readable in every skin", async ({
  page,
}) => {
  await page.goto("/");
  for (const theme of themes) {
    await choose(page, theme);
    await page.evaluate(() => {
      const toast = document.querySelector("#toast");
      toast.textContent = "配置已应用。";
      toast.hidden = false;
    });
    await contrast(page);
    await page.evaluate(() => {
      document.querySelector("#toast").hidden = true;
    });
  }
});
