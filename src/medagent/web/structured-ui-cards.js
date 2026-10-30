// Basic question / subject-change / card families.
import {
  UI_VERSION,
  illustrationSequence,
  temperatureGuide,
  sourceNames,
  qualityNames,
  inputStates,
  el,
  button,
  text,
  list,
  validateCalculationFollowup,
  renderCalculationFollowup,
  validateObservations,
  renderObservations,
  validateProvenance,
  sourceTurnContext,
  asset,
  replyEnvelope,
  calculationEnvelope,
  SCALE_FORM,
  SCALE_RENDERER,
  FORMULA_FORM,
  FORMULA_RENDERER,
  renderQuestion,
  validateQuestion,
  renderBodyMap,
  visualIcon,
  renderLocationAnswer,
  validateLocationAnswerSpec,
  renderLocationChips,
} from "./structured-ui-primitives.js";

export function installCards(register) {
  register("question", validateQuestion, renderQuestion);
  register(
    "subject_change",
    (data) => {
      for (const key of ["current", "requested"]) {
        text(data[key]?.label);
        text(data[key]?.category);
        if (
          !data[key].label ||
          data[key].label.length > 40 ||
          !["self", "child", "other_adult", "proxy", "unknown"].includes(
            data[key].category,
          )
        )
          throw new Error("Invalid consultation subject");
      }
      text(data.source_text);
      text(data.source_turn_id);
      if (!data.source_turn_id || data.source_text.length > 2000)
        throw new Error("Invalid subject source");
    },
    (data, context) => {
      const card = el("article", "subject-change-card");
      const pair = el("div", "subject-pair");
      for (const [key, label] of [
        ["current", "本次问诊"],
        ["requested", "另一位"],
      ]) {
        const person = el("div", `subject-person subject-${key}`);
        person.append(
          visualIcon(
            data[key].category === "self"
              ? "person"
              : data[key].category === "child"
                ? "child"
                : "family",
          ),
          el("small", "", label),
          el("strong", "", data[key].label),
        );
        pair.append(person);
        if (key === "current") {
          const arrow = el("span", "subject-arrow", "→");
          arrow.setAttribute("aria-hidden", "true");
          pair.append(arrow);
        }
      }
      const heading = el("h2", "question-title", "这次是为谁咨询？");
      const actions = el("div", "subject-actions");
      for (const [label, callback, style] of [
        [
          `为${data.requested.label}新建问诊`,
          () => context.onNewSubject?.(data.source_text, data.requested.label),
          "primary-button",
        ],
        [
          `继续${data.current.label}问诊`,
          () => context.onResumeSubject?.(),
          "text-button",
        ],
      ]) {
        const action = button(label, callback, `${style} subject-action`);
        action.dataset.actionId = context.actionId || "";
        action.disabled = Boolean(
          context.readOnly || !context.onNewSubject || !context.onResumeSubject,
        );
        actions.append(action);
      }
      const details = el("details", "calculation-source");
      details.append(
        el("summary", "", "查看刚才的原话"),
        el("blockquote", "calculation-evidence", data.source_text),
      );
      card.append(
        pair,
        heading,
        el(
          "p",
          "conversion-hint",
          `${data.current.label}的已记录信息会保留。新问诊会先让你核对原话。`,
        ),
        actions,
        details,
      );
      return card;
    },
  );
  register(
    "interaction_followup",
    validateCalculationFollowup,
    renderCalculationFollowup,
  );
  register(
    "subject_draft",
    (data) => {
      text(data.label);
      if (!data.label || data.label.length > 40)
        throw new Error("Invalid draft subject");
    },
    (data) => {
      const card = el("article", "subject-change-card subject-draft-card");
      const symbol = el("div", "question-symbol");
      symbol.append(visualIcon("family"));
      card.append(
        symbol,
        el("h2", "question-title", `核对${data.label}的原话`),
        el("p", "conversion-hint", "可在下方修改，再点击发送。"),
      );
      return card;
    },
  );
}
