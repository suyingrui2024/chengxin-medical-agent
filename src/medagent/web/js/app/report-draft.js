// Consultation report candidates and their confirmation dialog.
import { $, textElement as el, action as button } from "../dom.js";
import { state } from "./state.js";

export function createReportDraftUI({
  api,
  toast,
  setBusy,
  renderCase,
  formatTime,
}) {
  let draft = null;
  function renderReportItems(items) {
    const summary = $("#report-items-summary");
    summary.replaceChildren();
    summary.hidden = !items.length;
    if (!items.length) return;
    const heading = el("div", "report-items-heading");
    heading.append(
      el("span", "", "本次问诊使用的数据"),
      el("small", "", `${items.length} 项`),
    );
    const list = el("div", "report-items-list");
    items.forEach((item) => {
      const row = el("div", "report-item-row");
      const value = [item.reported_value, item.unit].filter(Boolean).join(" ");
      const provenance =
        item.source === "patient_record"
          ? [
              item.record_title,
              item.measured_at && formatTime(item.measured_at, true),
            ]
              .filter(Boolean)
              .join(" · ")
          : "";
      const label = [value, provenance].filter(Boolean).join(" · ");
      row.append(
        el("strong", "", item.name),
        el("span", "", label || "已确认"),
      );
      if (item.interpretation_note)
        row.append(el("small", "report-item-note", item.interpretation_note));
      row.append(
        button("移除", "fact-edit", () =>
          removeReportItem(item.report_item_id),
        ),
      );
      list.append(row);
    });
    summary.append(heading, list);
  }
  function reportItemText(item) {
    const parts = [
      item.name,
      item.reported_value,
      item.unit,
      item.reference_range,
    ].filter(Boolean);
    if (item.source === "patient_record") {
      if (item.record_title && item.record_title !== item.name)
        parts.push(item.record_title);
      if (item.measured_at) parts.push(formatTime(item.measured_at, true));
    }
    const text = parts.join(" · ");
    return item.interpretation_note
      ? `${text}（解读：${item.interpretation_note}）`
      : text;
  }
  function renderReportDraft(draft) {
    const target = $("#report-draft-result");
    target.replaceChildren();
    if (!draft) return;
    if (draft.missing_context?.length)
      target.append(
        el("p", "report-note", `还需要：${draft.missing_context.join("、")}`),
      );
    if (!draft.items?.length) {
      target.append(
        el(
          "p",
          "report-note",
          "未识别到可逐项核对的内容。可尝试按“项目 数值 单位 参考范围”分行粘贴。",
        ),
      );
      return;
    }
    target.append(el("h3", "report-result-title", "逐项确认"));
    const list = el("div", "report-draft-list");
    draft.items.forEach((item) => {
      const row = el("div", "report-draft-row");
      row.append(
        el("strong", "", item.name),
        el("p", "", reportItemText(item)),
      );
      if (item.source_excerpt)
        row.append(el("small", "", `原文：${item.source_excerpt}`));
      const confirmed = item.status === "confirmed";
      const confirm = button(
        confirmed ? "已确认" : "确认加入",
        "secondary-button",
        () => confirmReportItem(draft.draft_id, item.source_item_id),
      );
      confirm.dataset.confirmed = String(confirmed);
      confirm.disabled = confirmed || state.busy;
      row.append(confirm);
      list.append(row);
    });
    target.append(list);
  }
  async function openReportDraft() {
    if (!state.sessionId) return;
    $("#report-feedback").textContent = "";
    $("#report-dialog").showModal();
    $("#report-text").focus();
  }
  async function submitReportDraft(event) {
    event.preventDefault();
    if (!state.sessionId || state.busy) return;
    const text = $("#report-text").value.trim();
    if (!text) return;
    const feedback = $("#report-feedback");
    feedback.textContent = "";
    setBusy(true);
    try {
      const data = await api(`/api/sessions/${state.sessionId}/report-drafts`, {
        method: "POST",
        body: { text, format: $("#report-format").value },
      });
      draft = data.draft;
      renderReportDraft(data.draft);
      feedback.textContent = data.draft.items.length
        ? "请核对每一项后再加入本次记录。"
        : "尚未写入任何问诊记录。";
    } catch (error) {
      feedback.textContent = error.message;
      feedback.classList.add("error");
    } finally {
      setBusy(false);
    }
  }
  async function confirmReportItem(draftId, sourceItemId) {
    if (!state.sessionId || state.busy) return;
    setBusy(true);
    try {
      const data = await api(
        `/api/sessions/${state.sessionId}/report-drafts/${draftId}/confirm`,
        { method: "POST", body: { source_item_id: sourceItemId } },
      );
      renderCase(data.state);
      if (draft?.draft_id === draftId) {
        const item = draft.items.find(
          (entry) => entry.source_item_id === sourceItemId,
        );
        if (item) item.status = "confirmed";
        renderReportDraft(draft);
      }
      toast("已加入本次记录。可随时移除。");
    } catch (error) {
      toast(error.message);
    } finally {
      setBusy(false);
    }
  }
  async function removeReportItem(itemId) {
    if (!state.sessionId || state.busy) return;
    if (!window.confirm("从本次记录中移除此报告项目？")) return;
    setBusy(true);
    try {
      const data = await api(
        `/api/sessions/${state.sessionId}/report-items/${itemId}`,
        {
          method: "DELETE",
        },
      );
      renderCase(data.state);
      toast("已移除该报告项目。");
    } catch (error) {
      toast(error.message);
    } finally {
      setBusy(false);
    }
  }

  return {
    renderItems: renderReportItems,
    itemText: reportItemText,
    open: openReportDraft,
    submit: submitReportDraft,
    reset() {
      draft = null;
    },
  };
}
