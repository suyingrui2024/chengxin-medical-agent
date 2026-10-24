import { expect, test } from "@playwright/test";

// P2 前端（基线 / 成员 / 多种类测量 / 档案引用横幅）渲染用例。
// 注意：本文件只做只读渲染断言，不提交任何保存请求——
// 同批 patient-records.spec.mjs 对时间线条数有精确断言，
// 写入会互相干扰（成员与基线接口本身均已落地）。
test("patient records render baseline, members and multi-kind measurements", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("button", { name: /健康档案/ }).click();
  await expect(page.getByRole("heading", { name: "健康档案" })).toBeVisible();

  // 1. 基线信息区：表单字段齐全（字段名与后端约定一致）。
  const baseline = page.locator("#records-baseline");
  await expect(baseline).toBeVisible();
  await expect(
    baseline.getByRole("heading", { name: "基线信息" }),
  ).toBeVisible();
  await baseline.getByLabel("出生年", { exact: true }).fill("1980");
  // 注：性别是包裹式 label + select，label 文本会含选项文本，
  // exact 匹配不到，改用 id 定位（与测量种类同理）。
  await baseline.locator("#baseline-sex").selectOption("female");
  await baseline.getByLabel("身高（cm）", { exact: true }).fill("165");
  await baseline.getByLabel("体重（kg）", { exact: true }).fill("60");
  await baseline.getByLabel("慢病", { exact: true }).fill("高血压");
  await baseline.getByLabel("手术 / 住院史摘要").fill("无");
  await expect(
    baseline.getByRole("button", { name: "保存基线信息" }),
  ).toBeVisible();
  await expect(
    baseline.getByRole("button", { name: "添加过敏史" }),
  ).toBeVisible();
  await expect(
    baseline.getByRole("button", { name: "添加长期用药" }),
  ).toBeVisible();

  // 2. 成员区：本人分区展示（加载失败时仅显示本人，不伪造）。
  const members = page.locator("#records-members");
  await expect(members).toBeVisible();
  await expect(page.locator("#member-list")).toContainText("本人");
  await expect(members.getByRole("button", { name: "添加成员" })).toBeVisible();

  // 3. 趋势区按 kind 分组容器存在（无数据时隐藏，不做判断文案）。
  await expect(page.locator("#records-measurement-trends")).toHaveCount(1);

  // 4. 测量录入：五种齐全；血压切换出双值输入与 mmHg 单位。
  await page.getByRole("button", { name: "记录测量" }).click();
  const dialog = page.getByRole("dialog", { name: "记录一次测量" });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.locator("select#measurement-record-kind option"),
  ).toHaveCount(5);
  await dialog
    .locator("select#measurement-record-kind")
    .selectOption("blood_pressure");
  await expect(dialog.getByLabel("收缩压")).toBeVisible();
  await expect(dialog.getByLabel("舒张压")).toBeVisible();
  await expect(dialog.locator("select#measurement-record-unit")).toHaveValue(
    "mmHg",
  );
  await dialog.getByLabel("测量时间").fill("2026-08-30T07:20");
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toBeHidden();

  expect(errors).toEqual([]);
});

test("baseline banner hides on dismiss and fires its callback", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const module = await import("/patient-records-ui.js");
    let called = false;
    const banner = module.createBaselineBanner({
      onDismiss: () => {
        called = true;
      },
    });
    document.body.append(banner);
    const text = banner.textContent || "";
    banner.querySelector("button")?.click();
    return { id: banner.id, text, hidden: banner.hidden, called };
  });
  expect(result.id).toBe("chengxin-baseline-banner");
  expect(result.text).toContain("本次问诊参考了你的健康档案（慢病/过敏/用药）");
  expect(result.hidden).toBe(true);
  expect(result.called).toBe(true);
});
