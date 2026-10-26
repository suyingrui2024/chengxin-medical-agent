import { expect, test } from "@playwright/test";

// P4-D 就诊摘要打印页与导出：只读渲染断言，不发任何写请求、不改
// 对话框、不依赖后端 handoff v2（P4-A 尚未落地时，用合成 v2 数据
// 覆盖打印模块；v2 字段名见 handoff-print.js 底部的对齐点）。
const v2Handoff = {
  title: "就诊交接摘要（患者核对版）",
  limitations: "这是患者提供信息的整理，不构成诊断、治疗建议或临床检验复核。",
  conclusion: {
    disposition: {
      text: "今日内请到线下医疗机构就诊。",
      window_hours: 24,
      department_hint: "呼吸内科",
    },
    directions_visible: true,
    // 包 C 残留跟进（模块化重整 §4.4 fail-closed 门）：打印模块仅当
    // department_visible === true 时输出建议科室，可见路径夹具须显式带旗。
    department_visible: true,
    directions: [{ patient_label: "上呼吸道感染" }],
    narrative: {
      framing: "这次的不适值得当面让医生评估一次。",
      reasoning: "发热与咳嗽同时出现，医生需要当面判断。",
      severity: "目前看不算最紧急，但不宜久拖。",
    },
    self_care: {
      do: ["多休息", "多喝水"],
      avoid: ["不要自行服用抗生素"],
      measure: ["留意体温变化"],
      allergy_hint: "你档案里记有青霉素过敏，涉及用药请先核对",
    },
    return_now_if: ["呼吸困难加重", "意识不清"],
    follow_up: { kind: "urgent_checkin", due_in_hours: 24 },
  },
  baseline: {
    display_name: "本人",
    chronic_conditions: ["高血压"],
    allergies: [{ kind: "drug", name: "青霉素" }],
    medications: [{ name: "氨氯地平", purpose: "降压" }],
    surgical_history: "无",
  },
  facts: [
    { label: "主要问题", value: "咳嗽发热三天" },
    { label: "开始时间", value: "三天前" },
  ],
  report_items: [
    {
      name: "体温",
      reported_value: "38.5",
      unit: "°C",
      reference_range: "",
      record_title: "",
      measured_at: "",
    },
  ],
  needs_confirmation: [{ label: "过敏信息" }],
};

test("handoff print renders all v2 sections without horizontal overflow", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    const element = module.buildHandoffPrintElement(handoff);
    document.body.append(element);
    const headings = [...element.querySelectorAll("h2")].map((node) =>
      node.textContent.trim(),
    );
    const text = element.textContent;
    return {
      headings,
      hasDisclaimer: text.includes("AI 整理、未经医生审核、供就诊参考"),
      hasLimitation: text.includes("不构成诊断"),
      overflow: element.scrollWidth - element.clientWidth,
      hasPrintCss: module.PRINT_CSS.includes("@page"),
    };
  }, v2Handoff);
  expect(result.headings).toEqual([
    "结论",
    "档案基线",
    "已记录",
    "本次问诊使用的数据",
    "仍待补充",
    "自我照护要点",
    "出现以下情况请立即就医",
    "随访",
  ]);
  expect(result.hasDisclaimer).toBe(true);
  expect(result.hasLimitation).toBe(true);
  expect(result.hasPrintCss).toBe(true);
  expect(result.overflow).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
});

test("handoff print markdown/text share section order and v1 renders safely", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    const markdown = module.handoffPrintMarkdown(handoff);
    const text = module.handoffPrintText(handoff);
    // shadow 方向不得泄漏：directions_visible 为 false 时不输出方向行。
    const shadow = JSON.parse(JSON.stringify(handoff));
    shadow.conclusion.directions_visible = false;
    const shadowMarkdown = module.handoffPrintMarkdown(shadow);
    // shadow 科室不得泄漏：department_visible 为 false 时不输出科室行；
    // flag 缺失时按不可见处理（fail-closed），同样不输出。
    const shadowDept = JSON.parse(JSON.stringify(handoff));
    shadowDept.conclusion.department_visible = false;
    const shadowDeptMarkdown = module.handoffPrintMarkdown(shadowDept);
    const missingDept = JSON.parse(JSON.stringify(handoff));
    delete missingDept.conclusion.department_visible;
    const missingDeptMarkdown = module.handoffPrintMarkdown(missingDept);
    // v1 结构（无 conclusion/baseline）不崩溃，且仍有页脚声明。
    const v1 = {
      title: "就诊交接摘要（患者核对版）",
      limitations: "这是患者提供信息的整理。",
      facts: [{ label: "主要问题", value: "头痛" }],
      report_items: [],
      needs_confirmation: [],
    };
    const v1Element = module.buildHandoffPrintElement(v1);
    // 下载只断言文件名与不抛错：拦截真实下载。
    const originalCreate = URL.createObjectURL;
    const originalClick = HTMLAnchorElement.prototype.click;
    URL.createObjectURL = () => "blob:test";
    HTMLAnchorElement.prototype.click = function () {};
    let mdName = "";
    let txtName = "";
    try {
      mdName = module.downloadHandoffFile(handoff, "md");
      txtName = module.downloadHandoffFile(handoff, "txt");
    } finally {
      URL.createObjectURL = originalCreate;
      HTMLAnchorElement.prototype.click = originalClick;
    }
    return {
      markdown,
      text,
      shadowHasDirection: shadowMarkdown.includes("可能方向"),
      shadowDeptHasDepartment: shadowDeptMarkdown.includes("建议科室"),
      missingDeptHasDepartment: missingDeptMarkdown.includes("建议科室"),
      v1Headings: [...v1Element.querySelectorAll("h2")].map((node) =>
        node.textContent.trim(),
      ),
      v1HasDisclaimer:
        v1Element.textContent.includes("AI 整理、未经医生审核、供就诊参考"),
      mdName,
      txtName,
    };
  }, v2Handoff);
  const order = [
    "## 结论",
    "## 档案基线",
    "## 已记录",
    "## 本次问诊使用的数据",
    "## 仍待补充",
    "## 自我照护要点",
    "## 出现以下情况请立即就医",
    "## 随访",
  ];
  let lastIndex = -1;
  for (const heading of order) {
    const index = result.markdown.indexOf(heading);
    expect(index).toBeGreaterThan(lastIndex);
    lastIndex = index;
  }
  expect(result.markdown).toContain("处置：今日内请到线下医疗机构就诊。");
  expect(result.markdown).toContain("建议科室：呼吸内科");
  expect(result.markdown).toContain("过敏：药物·青霉素");
  expect(result.markdown).toContain("主要问题：咳嗽发热三天");
  expect(result.markdown).toContain("待补充：过敏信息");
  expect(result.markdown).toContain("AI 整理、未经医生审核、供就诊参考");
  expect(result.text).toContain("随访");
  expect(result.text).toContain("24 小时内");
  expect(result.shadowHasDirection).toBe(false);
  expect(result.shadowDeptHasDepartment).toBe(false);
  expect(result.missingDeptHasDepartment).toBe(false);
  expect(result.v1Headings).toEqual(["已记录"]);
  expect(result.v1HasDisclaimer).toBe(true);
  expect(result.mdName).toBe("就诊交接摘要.md");
  expect(result.txtName).toBe("就诊交接摘要.txt");
});

test("shadow narrative.reasoning with a direction label never leaks, visible reasoning still renders", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    // M2: the old shadow case used a reasoning sentence with no
    // direction label, so it could not catch the leak. Here the
    // reasoning itself carries a direction label.
    const shadow = JSON.parse(JSON.stringify(handoff));
    shadow.conclusion.directions_visible = false;
    shadow.conclusion.narrative.reasoning =
      "可能与上呼吸道感染有关，医生需要当面判断。";
    const shadowMarkdown = module.handoffPrintMarkdown(shadow);
    const shadowElement = module.buildHandoffPrintElement(shadow);
    const visible = JSON.parse(JSON.stringify(handoff));
    visible.conclusion.directions_visible = true;
    visible.conclusion.narrative.reasoning =
      "可能与上呼吸道感染有关，医生需要当面判断。";
    const visibleMarkdown = module.handoffPrintMarkdown(visible);
    return {
      shadowMarkdown,
      shadowText: shadowElement.textContent,
      visibleMarkdown,
    };
  }, v2Handoff);
  // Shadow: neither the reasoning line nor its direction label.
  expect(result.shadowMarkdown).not.toContain("原理：");
  expect(result.shadowMarkdown).not.toContain("上呼吸道感染");
  expect(result.shadowText).not.toContain("上呼吸道感染");
  // Shadow still keeps framing/severity (no direction in them).
  expect(result.shadowMarkdown).toContain("定性：");
  expect(result.shadowMarkdown).toContain("程度：");
  // Visible: reasoning renders as before.
  expect(result.visibleMarkdown).toContain(
    "原理：可能与上呼吸道感染有关，医生需要当面判断。",
  );
});

test("v1.3 mode gating: restricted shows directions + visit notes without reasoning, off hides directions", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    // restricted: composer order (rule_out first), rule_out prominent,
    // reasoning hidden, visit-notes section visible.
    const restricted = JSON.parse(JSON.stringify(handoff));
    restricted.conclusion.directions_mode = "restricted";
    restricted.conclusion.directions_visible = false;
    restricted.conclusion.directions = [
      { patient_label: "需排除项", group: "rule_out" },
      { patient_label: "更可能项", group: "likely" },
    ];
    restricted.conclusion.visit_notes = ["咳嗽三天", "担心是肺炎"];
    const restrictedMarkdown = module.handoffPrintMarkdown(restricted);
    // off: directions hidden even though the list is present (must not
    // trust upstream stripping); visit notes are not direction-gated.
    const off = JSON.parse(JSON.stringify(restricted));
    off.conclusion.directions_mode = "off";
    const offMarkdown = module.handoffPrintMarkdown(off);
    // on: existing behaviour — directions + reasoning render.
    const on = JSON.parse(JSON.stringify(restricted));
    on.conclusion.directions_mode = "on";
    on.conclusion.directions_visible = true;
    const onMarkdown = module.handoffPrintMarkdown(on);
    return {
      restrictedMarkdown,
      offMarkdown,
      onMarkdown,
    };
  }, v2Handoff);
  expect(result.restrictedMarkdown).toContain("需要排除的方向：需排除项");
  expect(result.restrictedMarkdown).toContain("可能方向：更可能项");
  expect(
    result.restrictedMarkdown.indexOf("需要排除的方向：需排除项"),
  ).toBeLessThan(result.restrictedMarkdown.indexOf("可能方向：更可能项"));
  expect(result.restrictedMarkdown).not.toContain("原理：");
  expect(result.restrictedMarkdown).toContain("就诊带话清单");
  expect(result.restrictedMarkdown).toContain("咳嗽三天");
  expect(result.offMarkdown).not.toContain("可能方向");
  expect(result.offMarkdown).not.toContain("需要排除的方向");
  expect(result.offMarkdown).not.toContain("原理：");
  expect(result.offMarkdown).toContain("就诊带话清单");
  expect(result.onMarkdown).toContain("可能方向：更可能项");
  expect(result.onMarkdown).toContain("原理：");
});

test("v1.3 department gate under restricted mode: flag true only", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    const shown = JSON.parse(JSON.stringify(handoff));
    shown.conclusion.directions_mode = "restricted";
    shown.conclusion.department_visible = true;
    const hidden = JSON.parse(JSON.stringify(handoff));
    hidden.conclusion.directions_mode = "restricted";
    hidden.conclusion.department_visible = false;
    const missing = JSON.parse(JSON.stringify(handoff));
    missing.conclusion.directions_mode = "restricted";
    delete missing.conclusion.department_visible;
    // Empty visit notes render no empty section.
    const emptyNotes = JSON.parse(JSON.stringify(handoff));
    emptyNotes.conclusion.visit_notes = [];
    return {
      shown: module.handoffPrintMarkdown(shown).includes("建议科室"),
      hidden: module.handoffPrintMarkdown(hidden).includes("建议科室"),
      missing: module.handoffPrintMarkdown(missing).includes("建议科室"),
      emptyNotesHasSection: module
        .handoffPrintMarkdown(emptyNotes)
        .includes("就诊带话清单"),
    };
  }, v2Handoff);
  expect(result.shown).toBe(true);
  expect(result.hidden).toBe(false);
  expect(result.missing).toBe(false);
  expect(result.emptyNotesHasSection).toBe(false);
});

test("interpretation note on a report item renders in markdown and DOM", async ({
  page,
}) => {
  await page.goto("/");
  const result = await page.evaluate(async (handoff) => {
    const module = await import("/handoff-print.js");
    const noted = JSON.parse(JSON.stringify(handoff));
    noted.report_items[0].interpretation_note =
      "体温 38.5 °C，无法判断；只作记录，不下判断";
    const markdown = module.handoffPrintMarkdown(noted);
    const element = module.buildHandoffPrintElement(noted);
    const plain = JSON.parse(JSON.stringify(handoff));
    const plainMarkdown = module.handoffPrintMarkdown(plain);
    return {
      markdown,
      text: element.textContent,
      plainMarkdown,
    };
  }, v2Handoff);
  expect(result.markdown).toContain(
    "解读：体温 38.5 °C，无法判断；只作记录，不下判断",
  );
  expect(result.text).toContain("解读：体温 38.5 °C，无法判断");
  expect(result.plainMarkdown).not.toContain("解读：");
});
