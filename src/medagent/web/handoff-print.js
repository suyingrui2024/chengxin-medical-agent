// 就诊交接摘要的打印友好呈现与 .md/.txt 导出。
//
// 只做呈现，不改数据口径：
// - 已有区（已记录 / 问诊使用的数据 / 仍待补充 / limitations）沿用
//   app.js handoffLines 的行文：`标签：值`、报告项以 ` · ` 连接、
//   待补充以 `待补充：A、B` 成行、limitations 收尾。
// - 新增区（结论 / 档案基线 / 自我照护 / 立即就医 / 随访）按设计
//   §6.1 的 patient-handoff/v2 结构读取，见底部的 P4-A 对齐点。
// - 本模块不向后端发任何请求；挂载到 handoff 对话框的工作留给
//   集成工人（挂载点与回调签名见模块底部注释）。
//
// 注意：directions 与 narrative.reasoning 只在
// conclusion.directions_visible === true 时渲染，否则一律不输出——
// shadow 中的方向不得经由打印/导出泄漏（reasoning 逐条含方向标签）。
// 建议科室同理：仅 conclusion.department_visible === true 时渲染
// disposition.department_hint（模块化重整 §4.4；flag 缺失即不可见，
// 不得因“hint 在场”而输出，也不得信任上游端点已剥离）。

export const PRINT_DISCLAIMER = "AI 整理、未经医生审核、供就诊参考";

export const PRINT_CSS = `
@page { size: A4; margin: 14mm 15mm; }
.handoff-print { font-family: inherit; color: #111; font-size: 12px; line-height: 1.5; max-width: 210mm; margin: 0 auto; }
.handoff-print h1 { font-size: 20px; margin: 0 0 2mm; }
.handoff-print .handoff-print-subtitle { color: #555; margin: 0 0 5mm; }
.handoff-print section { margin: 0 0 4mm; }
.handoff-print h2 { font-size: 14px; margin: 0 0 1.5mm; padding-bottom: 1mm; border-bottom: 1px solid #ccc; }
.handoff-print p { margin: 0 0 1mm; }
.handoff-print ul { margin: 0; padding-left: 5mm; }
.handoff-print li { margin: 0 0 1mm; }
.handoff-print .handoff-print-footer { margin-top: 6mm; padding-top: 3mm; border-top: 1px solid #999; color: #444; break-inside: avoid; }
.handoff-print .handoff-print-conclusion { break-inside: avoid; }
@media print {
  body { margin: 0; }
  .handoff-print { max-width: none; }
}
`;

const ALLERGY_KIND_LABELS = { drug: "药物", food: "食物", other: "其他" };
const FOLLOW_UP_KIND_LABELS = {
  urgent_checkin: "紧急回访",
  routine_checkin: "例行回访",
};

function handoffValue(value) {
  return typeof value === "object" && value !== null
    ? JSON.stringify(value)
    : String(value ?? "");
}

function isNonEmpty(value) {
  return value !== undefined && value !== null && value !== "";
}

function asList(value) {
  return Array.isArray(value) ? value : [];
}

function allergyText(item) {
  if (typeof item === "string") return item;
  const kind = ALLERGY_KIND_LABELS[item?.kind] || "";
  return [kind, item?.name].filter(Boolean).join("·");
}

function medicationText(item) {
  if (typeof item === "string") return item;
  return (
    [item?.name, item?.purpose].filter(Boolean).join("（用途：") +
    (item?.purpose ? "）" : "")
  );
}

function reportItemText(item) {
  return [
    item.name,
    item.reported_value,
    item.unit,
    item.reference_range,
    item.record_title && item.record_title !== item.name
      ? item.record_title
      : "",
    item.measured_at || "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function dispositionOf(conclusion) {
  const disposition = conclusion?.disposition;
  if (typeof disposition === "string") return { text: disposition };
  return disposition || null;
}

// 把 handoff 归一化为有序的呈现区：每区 { heading, lines }，lines 为
// 纯文本行。DOM 渲染与 md/txt 导出共用这一份区数据，不另造口径。
export function handoffPrintSections(handoff) {
  const data = handoff || {};
  const sections = [];
  const conclusion = data.conclusion || null;

  if (conclusion) {
    const lines = [];
    const disposition = dispositionOf(conclusion);
    if (disposition?.text) lines.push(`处置：${disposition.text}`);
    if (disposition?.window_hours)
      lines.push(`建议窗口：${disposition.window_hours} 小时内`);
    // 科室与方向同理，单源契约（模块化重整 §4.4）：仅
    // department_visible === true 时输出；只凭“hint 在场”不输出，
    // flag 缺失时按不可见处理（fail-closed）——不得信任上游已剥离。
    if (conclusion.department_visible === true && disposition?.department_hint)
      lines.push(`建议科室：${disposition.department_hint}`);
    const narrative = conclusion.narrative || null;
    if (narrative?.framing) lines.push(`定性：${narrative.framing}`);
    // reasoning 逐条含方向标签（见 conclusion._narrative_reasoning），
    // 故与 directions 同门控：仅 directions_visible 时输出，shadow 下
    // framing/severity 可保留（不含方向）。
    if (conclusion.directions_visible === true && narrative?.reasoning)
      lines.push(`原理：${narrative.reasoning}`);
    if (narrative?.severity) lines.push(`程度：${narrative.severity}`);
    // 仅可见时输出方向；shadow 方向不在此出现。
    if (conclusion.directions_visible === true) {
      asList(conclusion.directions).forEach((direction) => {
        const label =
          direction?.patient_label || direction?.hypothesis_id || "";
        if (label) lines.push(`可能方向：${label}`);
      });
    }
    if (lines.length) sections.push({ heading: "结论", lines });
  }

  const baseline = data.baseline || null;
  if (baseline) {
    const lines = [];
    if (isNonEmpty(baseline.display_name))
      lines.push(`档案对象：${baseline.display_name}`);
    if (asList(baseline.chronic_conditions).length)
      lines.push(`慢病：${baseline.chronic_conditions.join("、")}`);
    if (asList(baseline.allergies).length)
      lines.push(
        `过敏：${baseline.allergies.map(allergyText).filter(Boolean).join("、")}`,
      );
    if (asList(baseline.medications).length)
      lines.push(
        `长期用药：${baseline.medications.map(medicationText).filter(Boolean).join("、")}`,
      );
    if (isNonEmpty(baseline.surgical_history))
      lines.push(`手术 / 住院史：${baseline.surgical_history}`);
    if (lines.length) sections.push({ heading: "档案基线", lines });
  }

  const facts = asList(data.facts).map(
    (item) => `${item.label}：${handoffValue(item.value)}`,
  );
  if (facts.length) sections.push({ heading: "已记录", lines: facts });

  const reportItems = asList(data.report_items).flatMap((item) => {
    const lines = [reportItemText(item)];
    if (item.interpretation_note)
      lines.push(`解读：${item.interpretation_note}`);
    return lines;
  });
  if (reportItems.length)
    sections.push({ heading: "本次问诊使用的数据", lines: reportItems });

  const needs = asList(data.needs_confirmation).map((item) => item.label);
  if (needs.length)
    sections.push({
      heading: "仍待补充",
      lines: [`待补充：${needs.join("、")}`],
    });

  const care = conclusion?.self_care || null;
  if (care) {
    const lines = [];
    asList(care.do).forEach((line) => lines.push(`建议做：${line}`));
    asList(care.avoid).forEach((line) => lines.push(`避免：${line}`));
    asList(care.measure).forEach((line) => lines.push(`留意测量：${line}`));
    if (isNonEmpty(care.allergy_hint)) lines.push(care.allergy_hint);
    if (lines.length) sections.push({ heading: "自我照护要点", lines });
  }

  if (asList(conclusion?.return_now_if).length)
    sections.push({
      heading: "出现以下情况请立即就医",
      lines: asList(conclusion.return_now_if),
    });

  const followUp = data.follow_up || conclusion?.follow_up || null;
  if (followUp && (followUp.kind || followUp.due_in_hours || followUp.reason)) {
    const lines = [];
    const kind = FOLLOW_UP_KIND_LABELS[followUp.kind] || followUp.kind || "";
    if (kind) lines.push(`类型：${kind}`);
    if (followUp.due_in_hours)
      lines.push(`时间：${followUp.due_in_hours} 小时内`);
    if (isNonEmpty(followUp.reason)) lines.push(followUp.reason);
    sections.push({ heading: "随访", lines });
  }

  return sections;
}

export function handoffPrintTitle(handoff) {
  return handoff?.title || "就诊交接摘要";
}

export function handoffPrintMarkdown(handoff) {
  const lines = [`# ${handoffPrintTitle(handoff)}`, ""];
  for (const section of handoffPrintSections(handoff)) {
    lines.push(`## ${section.heading}`, "");
    section.lines.forEach((line) => lines.push(`- ${line}`));
    lines.push("");
  }
  if (isNonEmpty(handoff?.limitations)) lines.push(handoff.limitations, "");
  lines.push(PRINT_DISCLAIMER);
  return lines.join("\n");
}

export function handoffPrintText(handoff) {
  const lines = [handoffPrintTitle(handoff), ""];
  for (const section of handoffPrintSections(handoff)) {
    lines.push(section.heading, ...section.lines, "");
  }
  if (isNonEmpty(handoff?.limitations)) lines.push(handoff.limitations, "");
  lines.push(PRINT_DISCLAIMER);
  return lines.join("\n");
}

export function handoffPrintFilename(format) {
  return format === "md" ? "就诊交接摘要.md" : "就诊交接摘要.txt";
}

// 渲染打印页的 DOM 元素（可直接放入新窗口或页内容器）。
// 元素本身不含任何导航 chrome；打印样式由 PRINT_CSS 提供。
export function buildHandoffPrintElement(handoff) {
  const root = document.createElement("article");
  root.className = "handoff-print";
  const title = document.createElement("h1");
  title.textContent = handoffPrintTitle(handoff);
  root.append(title);
  const subtitle = document.createElement("p");
  subtitle.className = "handoff-print-subtitle";
  subtitle.textContent = "患者核对版 · 请在就诊时自行补充或更正";
  root.append(subtitle);
  for (const section of handoffPrintSections(handoff)) {
    const element = document.createElement("section");
    const heading = document.createElement("h2");
    heading.textContent = section.heading;
    element.append(heading);
    const list = document.createElement("ul");
    section.lines.forEach((line) => {
      const item = document.createElement("li");
      item.textContent = line;
      list.append(item);
    });
    element.append(list);
    if (section.heading === "结论")
      element.classList.add("handoff-print-conclusion");
    root.append(element);
  }
  const footer = document.createElement("footer");
  footer.className = "handoff-print-footer";
  if (isNonEmpty(handoff?.limitations)) {
    const limitation = document.createElement("p");
    limitation.textContent = handoff.limitations;
    footer.append(limitation);
  }
  const disclaimer = document.createElement("p");
  disclaimer.textContent = PRINT_DISCLAIMER;
  footer.append(disclaimer);
  root.append(footer);
  return root;
}

// 打开独立打印窗口并写入完整文档；调用方随后可调用 window.print()。
// 返回新窗口对象（被拦截时为 null），不抛错。
export function openHandoffPrintWindow(handoff) {
  const opened = window.open("", "_blank");
  if (!opened) return null;
  const element = buildHandoffPrintElement(handoff);
  opened.document.write(
    `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">` +
      `<title>${handoffPrintTitle(handoff)}</title>` +
      `<style>${PRINT_CSS}</style></head><body>` +
      element.outerHTML +
      `</body></html>`,
  );
  opened.document.close();
  return opened;
}

// 触发 .md/.txt 文件下载，返回文件名。下载本身不经过后端。
export function downloadHandoffFile(handoff, format) {
  const text =
    format === "md" ? handoffPrintMarkdown(handoff) : handoffPrintText(handoff);
  const blob = new Blob([text], {
    type:
      format === "md"
        ? "text/markdown;charset=utf-8"
        : "text/plain;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = handoffPrintFilename(format);
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return anchor.download;
}

// ── 给集成工人的挂载点（本模块不改 app.js，对话框保留） ─────────────
// 位置：src/medagent/web/index.html 的 #handoff-dialog .dialog-footer 内，
//   在 #copy-handoff 旁新增两个按钮：
//     <button id="print-handoff" type="button">打印摘要</button>
//     <button id="export-handoff-md" type="button">导出 .md</button>
//     <button id="export-handoff-txt" type="button">导出 .txt</button>
// 位置：src/medagent/web/app.js，openHandoff() 成功后保存当前 handoff
//   对象（现有 state.handoffText 只存文本，打印/导出需要结构化对象，
//   建议新增 state.handoff = data.handoff），并在文件顶部 import：
//     import { openHandoffPrintWindow, downloadHandoffFile }
//       from "./handoff-print.js";
// 回调签名（均在对话框按钮的 click 监听里调用，不重构对话框）：
//   print:  () => { const win = openHandoffPrintWindow(state.handoff);
//                    if (win) win.print(); }
//   md:     () => downloadHandoffFile(state.handoff, "md")
//   txt:    () => downloadHandoffFile(state.handoff, "txt")
//   三个回调在 state.handoff 为空时均不做任何事（由集成工人加守卫）。
// ── 给 P4-A 的对齐点（v2 字段名） ─────────────────────────────────
// 本模块按以下 v2 结构读取，P4-A 落地 build_handoff v2 时请保持这些
// 键名，或由集成工人在 openHandoff 处做一次映射，不要另起第二套：
//   handoff.conclusion: { disposition: { text, window_hours,
//     department_hint }, directions_visible: bool,
//     department_visible: bool, directions:
//     [{ patient_label | hypothesis_id }], narrative: { framing,
//     reasoning, severity }, self_care: { do, avoid, measure,
//     allergy_hint }, return_now_if: [str], follow_up: { kind,
//     due_in_hours } }
//   （department_visible 由交接端点透传；本模块按 === true 严格判断，
//   缺失即不可见——见模块头注释的单源契约。）
//   handoff.baseline: { display_name, chronic_conditions: [str],
//     allergies: [{ kind: drug|food|other, name } | str], medications:
//     [{ name, purpose } | str], surgical_history: str }
//   handoff.follow_up: { kind: urgent_checkin|routine_checkin,
//     due_in_hours, reason }（无此顶层键时回落到 conclusion.follow_up）
//   其余键（title / facts[{label,value}] / report_items /
//   needs_confirmation[{label}] / limitations）与 v1 完全相同。
