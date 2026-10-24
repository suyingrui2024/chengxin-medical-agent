import { expect, test } from "@playwright/test";

// P3 前端（报告解读节 + 识别轮询约定）渲染用例。
// 注意：后端解读接口尚未落地，本文件不伪造后端成功——
// 解读节用模块级纯渲染函数 + 固定 fixture 断言；
// 页面级用例只做只读加载，不提交任何写入请求。
test("interpretation section renders grades, explanations and disclaimer", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const module = await import("/patient-records-ui.js");
    const section = module.createInterpretationSection({
      // 后端 report-interpretation/v1 的 summary 是对象（text 在内）。
      summary: { text: "整体来看有两项需要注意，其余在参考区间内。" },
      items: [
        {
          display_name: "空腹血糖",
          result_text: "6.8 mmol/L",
          grade: "黄",
          explanation: "比参考区间上限高一点，先观察，不用紧张。",
          action: "复查",
        },
        {
          display_name: "总胆固醇",
          result_text: "4.2 mmol/L",
          grade: "绿",
          explanation: "在这个区间内。",
          action: "观察",
        },
        {
          display_name: "白细胞",
          result_text: "12.1 ×10⁹/L",
          grade: "红",
          explanation: "明显高于参考区间，建议尽快让医生看一下。",
          action: "尽快就诊",
        },
        {
          display_name: "维生素 D",
          result_text: "30 ng/mL",
          grade: "无法判断",
          explanation: "没有可用的参考区间，无法分级。",
          action: "复查",
        },
      ],
    });
    document.body.append(section);
    const dots = [...section.querySelectorAll(".interp-dot")].map(
      (dot) => dot.className,
    );
    return {
      text: section.textContent || "",
      dots,
      poll: module.RECOGNITION_POLL,
    };
  });
  expect(result.text).toContain("整体来看有两项需要注意");
  expect(result.text).toContain("比参考区间上限高一点");
  expect(result.text).toContain("建议尽快让医生看一下");
  expect(result.text).toContain(
    "由 AI 生成，未经医生审核；分级依据为工程版参考区间",
  );
  expect(result.text).toContain("建议：复查");
  expect(result.text).toContain("建议：尽快就诊");
  expect(result.dots).toEqual([
    expect.stringContaining("interp-dot--yellow"),
    expect.stringContaining("interp-dot--green"),
    expect.stringContaining("interp-dot--red"),
    expect.stringContaining("interp-dot--unknown"),
  ]);
  // 轮询约定：2 秒间隔、45 次上限（90 秒），写死在模块导出里。
  expect(result.poll).toEqual({ intervalMs: 2000, maxAttempts: 45 });
});

test("unknown interpretation grade falls back to grey unjudged", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const module = await import("/patient-records-ui.js");
    const section = module.createInterpretationSection({
      items: [
        {
          display_name: "某项",
          result_text: "1",
          grade: "blue",
          explanation: "分级值不在约定内。",
        },
      ],
    });
    document.body.append(section);
    const dot = section.querySelector(".interp-dot");
    return { text: section.textContent || "", dot: dot?.className || "" };
  });
  expect(result.text).toContain("无法判断");
  expect(result.dot).toContain("interp-dot--unknown");
});

test("records page still loads with interpretation code present", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "开始问诊", exact: true }).click();
  await page.getByRole("button", { name: "我自己", exact: true }).click();
  await page.getByRole("button", { name: /健康档案/ }).click();
  await expect(page.getByRole("heading", { name: "健康档案" })).toBeVisible();
  expect(errors).toEqual([]);
});
