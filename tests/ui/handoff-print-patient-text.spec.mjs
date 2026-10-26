import { expect, test } from "@playwright/test";

// E2E-P1-1 / E2E-P2-2：交接打印的科室行与事实行患者化文案。
// 打印视图、.md 导出、.txt 导出共用 handoffPrintSections，故三路
// 在此同断言；门控语义不动（仅 department_visible === true 时输出）。
const e2eHandoff = {
  title: "就诊摘要（预问诊版）",
  limitations: "本摘要由 AI 根据本次问诊整理，未经医生审核。",
  conclusion: {
    disposition: {
      text: "可以在家先观察",
      window_hours: 48,
      department_hint: {
        department: "全科医疗科",
        reason: "目前还分不清属于哪个专科的问题，建议先到全科医疗科评估",
      },
    },
    department_visible: true,
    directions_mode: "restricted",
    directions: [],
  },
  facts: [
    { label: "咨询对象", value: "self", assertion: "present" },
    { label: "急症筛查", value: false, assertion: "absent" },
    { label: "年龄", value: 73 },
  ],
  report_items: [],
  needs_confirmation: [],
};

test("object department hint renders as readable text on all three paths", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    const markdown = module.handoffPrintMarkdown(handoff);
    const text = module.handoffPrintText(handoff);
    const element = module.buildHandoffPrintElement(handoff);
    return {
      markdown,
      text,
      domText: element.textContent,
    };
  }, e2eHandoff);
  const expected =
    "建议科室：全科医疗科 —— 目前还分不清属于哪个专科的问题，建议先到全科医疗科评估";
  expect(result.markdown).toContain(expected);
  expect(result.text).toContain(expected);
  expect(result.domText).toContain(expected);
  for (const output of [result.markdown, result.text, result.domText])
    expect(output).not.toContain("[object Object]");
});

test("department gate stays fail-closed for object hints", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    const hidden = JSON.parse(JSON.stringify(handoff));
    hidden.conclusion.department_visible = false;
    const missing = JSON.parse(JSON.stringify(handoff));
    delete missing.conclusion.department_visible;
    const stringHint = JSON.parse(JSON.stringify(handoff));
    stringHint.conclusion.disposition.department_hint = "呼吸内科";
    const reasonLess = JSON.parse(JSON.stringify(handoff));
    reasonLess.conclusion.disposition.department_hint = {
      department: "口腔科",
    };
    return {
      hidden: module.handoffPrintMarkdown(hidden),
      missing: module.handoffPrintMarkdown(missing),
      stringHint: module.handoffPrintMarkdown(stringHint),
      reasonLess: module.handoffPrintMarkdown(reasonLess),
    };
  }, e2eHandoff);
  expect(result.hidden).not.toContain("建议科室");
  expect(result.hidden).not.toContain("[object Object]");
  expect(result.missing).not.toContain("建议科室");
  expect(result.missing).not.toContain("[object Object]");
  expect(result.stringHint).toContain("建议科室：呼吸内科");
  expect(result.reasonLess).toContain("建议科室：口腔科");
});

test("fact rows use patient wording for verified values only", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    const base = JSON.parse(JSON.stringify(handoff));
    // agent 固定选项路径：ABSENT 时 value 为 null，同样是否认。
    const absentNull = JSON.parse(JSON.stringify(handoff));
    absentNull.facts[1] = {
      label: "急症筛查",
      value: null,
      assertion: "absent",
    };
    const present = JSON.parse(JSON.stringify(handoff));
    present.facts[1] = {
      label: "急症筛查",
      value: true,
      assertion: "present",
    };
    const childCase = JSON.parse(JSON.stringify(handoff));
    childCase.facts[0] = {
      label: "咨询对象",
      value: "child",
      assertion: "present",
    };
    const otherAdult = JSON.parse(JSON.stringify(handoff));
    otherAdult.facts[0] = {
      label: "咨询对象",
      value: "other_adult",
      assertion: "present",
    };
    // 未核实映射的值留原样（此处以性别代码值为例，不臆测）。
    const unmapped = JSON.parse(JSON.stringify(handoff));
    unmapped.facts.push({ label: "性别", value: "male" });
    return {
      base: module.handoffPrintMarkdown(base),
      absentNull: module.handoffPrintMarkdown(absentNull),
      present: module.handoffPrintMarkdown(present),
      childCase: module.handoffPrintMarkdown(childCase),
      otherAdult: module.handoffPrintMarkdown(otherAdult),
      unmapped: module.handoffPrintMarkdown(unmapped),
    };
  }, e2eHandoff);
  expect(result.base).toContain("咨询对象：本人");
  expect(result.base).toContain("急症筛查：已否认上述危险信号");
  expect(result.base).not.toContain("咨询对象：self");
  expect(result.base).not.toContain("急症筛查：false");
  expect(result.absentNull).toContain("急症筛查：已否认上述危险信号");
  expect(result.present).toContain("急症筛查：报告了紧急信号");
  expect(result.childCase).toContain("咨询对象：孩子");
  expect(result.otherAdult).toContain("咨询对象：其他家人");
  expect(result.unmapped).toContain("性别：male");
});
