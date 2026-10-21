import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const axeSource = await readFile(
  new URL("../../node_modules/axe-core/axe.min.js", import.meta.url),
  "utf8",
);

test("patient records keep dated measurements editable and archive images locally", async ({
  page,
}, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("button", { name: /健康档案/ }).click();
  await expect(page.getByRole("heading", { name: "健康档案" })).toBeVisible();

  await page.getByRole("button", { name: "记录血糖" }).click();
  const glucose = page.getByRole("dialog", { name: "记录一次血糖" });
  await glucose.getByLabel("数值").fill("6.8");
  await glucose.getByLabel("测量时间").fill("2026-08-30T07:20");
  await glucose.getByLabel("测量场景").selectOption("fasting");
  await glucose.getByRole("button", { name: "保存记录" }).click();
  await expect(glucose).toBeHidden();
  await expect(page.locator("#records-trend")).toContainText("6.8 mmol/L");

  await page.getByRole("button", { name: /血糖自测/ }).click();
  const detail = page.getByRole("dialog", { name: "血糖自测" });
  await expect(detail).toContainText("患者手动录入");
  await detail.getByRole("button", { name: "编辑", exact: true }).click();
  await detail.getByLabel("当前内容").fill("6.6");
  await detail.getByRole("button", { name: "保存修改" }).click();
  await expect(detail).toContainText("6.6 mmol/L");
  await expect(detail.getByText("2 个版本")).toBeVisible();
  await expect(detail).toContainText("只会带入你已经确认的数据");
  await detail.getByRole("button", { name: "带入 1 项数据" }).click();
  await expect(page.locator("#view-consultation")).toBeVisible();
  await expect(page.locator("#report-items-summary")).toContainText("6.6");
  await expect(page.locator("#report-items-summary")).toContainText("血糖自测");
  await page.getByRole("button", { name: /健康档案/ }).click();

  await page.getByRole("button", { name: "上传医疗图片" }).click();
  const upload = page.getByRole("dialog", { name: "上传一张医疗图片" });
  const image = await readFile(
    new URL(
      "../../docs/assets/minimal-iteration/temperature-small.png",
      import.meta.url,
    ),
  );
  await upload.getByLabel("图片").setInputFiles({
    name: "blood-test.png",
    mimeType: "image/png",
    buffer: image,
  });
  await upload.getByLabel("记录名称").fill("8月20日血常规");
  await upload.getByLabel("实际检查或测量时间").fill("2026-08-20T08:00");
  await upload.getByRole("button", { name: "归档并识别" }).click();
  await expect(upload).toBeHidden();
  await expect(
    page.getByRole("dialog", { name: "8月20日血常规" }),
  ).toContainText("当前还没有识别结果");
  await page
    .getByRole("dialog", { name: "8月20日血常规" })
    .getByRole("button", {
      name: "关闭",
    })
    .click();

  await expect(page.locator("#records-timeline .record-card")).toHaveCount(2);
  await expect(page.locator("#records-timeline")).toContainText(
    "8月20日血常规",
  );
  await page.screenshot({
    path: testInfo.outputPath("patient-records-timeline.png"),
    fullPage: true,
  });
  await page.route("**/patient-records-audit.js", (route) =>
    route.fulfill({ contentType: "application/javascript", body: axeSource }),
  );
  await page.addScriptTag({ url: "/patient-records-audit.js" });
  const violations = await page.evaluate(async () => {
    const result = await window.axe.run({
      runOnly: {
        type: "rule",
        values: ["button-name", "label", "aria-dialog-name"],
      },
    });
    return result.violations.map((item) => item.id);
  });
  expect(violations).toEqual([]);
  expect(errors).toEqual([]);
});
