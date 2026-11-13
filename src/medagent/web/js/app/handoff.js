// Own the active consultation's handoff preview, copy, print and exports.
import { $, textElement as el } from "../dom.js";
import { state } from "./state.js";
import {
  openHandoffPrintWindow,
  downloadHandoffFile,
} from "./handoff-print.js";

export function createHandoffUI({ api, toast, reportItemText }) {
  let handoff = null;
  function handoffValue(value) {
    return typeof value === "object"
      ? JSON.stringify(value)
      : String(value ?? "");
  }
  function handoffLines(handoff) {
    const rows = handoff.facts.map(
      (item) => `${item.label}：${handoffValue(item.value)}`,
    );
    if (handoff.report_items.length) {
      rows.push("本次问诊使用的数据：");
      handoff.report_items.forEach((item) =>
        rows.push(`- ${reportItemText(item)}`),
      );
    }
    if (handoff.needs_confirmation.length)
      rows.push(
        `待补充：${handoff.needs_confirmation.map((item) => item.label).join("、")}`,
      );
    return [handoff.title, ...rows, handoff.limitations].join("\n");
  }
  function renderHandoff(handoff) {
    const target = $("#handoff-content");
    target.replaceChildren();
    const addSection = (title, items, renderer) => {
      if (!items.length) return;
      const section = el("section", "handoff-section");
      section.append(el("h3", "", title));
      const list = el("div", "handoff-list");
      items.forEach((item) => list.append(renderer(item)));
      section.append(list);
      target.append(section);
    };
    addSection("已记录", handoff.facts, (item) =>
      el("p", "", `${item.label}：${handoffValue(item.value)}`),
    );
    addSection("本次问诊使用的数据", handoff.report_items, (item) =>
      el("p", "", reportItemText(item)),
    );
    addSection("仍待补充", handoff.needs_confirmation, (item) =>
      el("p", "", item.label),
    );
    target.append(el("p", "handoff-limitation", handoff.limitations));
  }
  async function openHandoff() {
    if (!state.sessionId) return;
    const sessionId = state.sessionId;
    try {
      const data = await api(`/api/sessions/${sessionId}/handoff`);
      if (state.sessionId !== sessionId) return;
      // 渲染单源（模块化重整 §4.4）：方向/科室可见性只消费交接端点
      // 透传的 mode/flag，不再从 live sheet 推导、不再补写 flag，更不再
      // 回落为“无 live sheet 时有方向即视为可见”（该回落分支已删除）。
      // 端点输出无方向即不可见；flag 缺失时按不可见处理
      // （fail-closed，打印模块的 === true 判断兜底）。narrative 仍
      // 取自 live sheet（端点不携带），但 reasoning 只按端点 mode 抄写。
      // 对话框渲染仍用原始端点对象（见上）。
      const sheet = state.case?.conclusion || null;
      const rawConclusion = data.handoff?.conclusion || null;
      // v1.3 §13.6：抄写层与结论卡同源，按端点透传的 directions_mode
      // 门控（与打印模块同一判定；抄写层再收紧一道，不信任上游已
      // 剥离）：restricted/on 方向可见、仅 on 推理线可见、off 方向
      // 整体不抄（置 null，保持端点 off 形态）；mode 缺失的旧端点
      // 回落到 directions_visible 的全量含义；未知 mode 按不可见
      // 处理。科室 hint 仅当 department_visible === true 时抄写；
      // 带话段原样透传（有项才渲染是打印层的规则）。
      const handoffMode = rawConclusion?.directions_mode;
      const hasHandoffMode =
        typeof handoffMode === "string" && handoffMode !== "";
      const directionsShown = hasHandoffMode
        ? handoffMode === "restricted" || handoffMode === "on"
        : rawConclusion?.directions_visible === true;
      const reasoningShown = hasHandoffMode
        ? handoffMode === "on"
        : rawConclusion?.directions_visible === true;
      // M2 纵深防御：narrative.reasoning 逐条含方向标签，仅全量模式
      // 抄进 handoff（打印模块另有同门控，双重保险）；framing /
      // severity 不含方向，可保留。
      const narrative = sheet?.narrative
        ? {
            framing: sheet.narrative.framing,
            severity: sheet.narrative.severity,
            ...(reasoningShown ? { reasoning: sheet.narrative.reasoning } : {}),
          }
        : undefined;
      let copiedConclusion = null;
      if (rawConclusion) {
        copiedConclusion = {
          ...rawConclusion,
          narrative,
          directions: directionsShown ? rawConclusion.directions : null,
        };
        if (
          rawConclusion.department_visible !== true &&
          rawConclusion.disposition
        ) {
          const restDisposition = { ...rawConclusion.disposition };
          delete restDisposition.department_hint;
          copiedConclusion.disposition = restDisposition;
        }
      }
      handoff = data.handoff
        ? {
            ...data.handoff,
            conclusion: copiedConclusion,
          }
        : null;
      renderHandoff(data.handoff);
      $("#handoff-dialog").showModal();
    } catch (error) {
      toast(error.message);
    }
  }
  async function copyHandoff() {
    if (!handoff) return;
    const text = handoffLines(handoff);
    try {
      await navigator.clipboard.writeText(text);
      toast("摘要已复制。");
    } catch {
      const area = el("textarea");
      area.value = text;
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      toast("摘要已复制。");
    }
  }

  return {
    open: openHandoff,
    copy: copyHandoff,
    reset() {
      handoff = null;
    },
    print() {
      if (handoff) openHandoffPrintWindow(handoff)?.print();
    },
    download(format) {
      if (handoff) downloadHandoffFile(handoff, format);
    },
  };
}
