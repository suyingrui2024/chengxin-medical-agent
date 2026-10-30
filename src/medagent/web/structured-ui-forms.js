// Scale and formula forms (returns renderScaleResult).
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

export function installForms(register) {
  function scaleDraftItems(data) {
    const items = list(data.items);
    if (!items.length || items.length > 60)
      throw new Error("Invalid scale items");
    const seen = new Set();
    return items.map((item) => {
      if (typeof item.id !== "string" || !item.id || seen.has(item.id))
        throw new Error("Invalid scale item id");
      seen.add(item.id);
      text(item.label);
      const options = list(item.options);
      if (!options.length || options.length > 12)
        throw new Error("Invalid scale options");
      const values = new Set();
      const normalised = options.map((option) => {
        if (!Number.isInteger(option.value) || values.has(option.value))
          throw new Error("Invalid scale option value");
        values.add(option.value);
        text(option.label);
        return { value: option.value, label: option.label };
      });
      return { id: item.id, label: item.label, options: normalised };
    });
  }

  function validateScaleDraft(data) {
    const items = scaleDraftItems(data);
    if (
      !data.input ||
      typeof data.input !== "object" ||
      Array.isArray(data.input)
    )
      throw new Error("Invalid scale input");
    const ids = new Set(items.map((item) => item.id));
    for (const [key, value] of Object.entries(data.input)) {
      if (!ids.has(key) || !Number.isInteger(value))
        throw new Error("Invalid scale answer");
    }
    if (list(data.unit_options).length)
      throw new Error("Scale draft must not carry units");
    if (data.progress !== undefined) {
      if (
        !Number.isInteger(data.progress.answered) ||
        !Number.isInteger(data.progress.total) ||
        data.progress.total !== items.length
      )
        throw new Error("Invalid scale progress");
    }
    return items;
  }

  function renderScaleForm(data, context) {
    const items = scaleDraftItems(data);
    const card = el("article", "patient-calculation-input scale-form");
    const symbol = el("div", "question-symbol");
    symbol.append(visualIcon("note"));
    const progress = el("p", "scale-progress");
    progress.setAttribute("role", "status");
    card.append(
      symbol,
      el("p", "task-kicker", "量表自评"),
      el("h2", "question-title", data.name),
      el("p", "scale-intro", "请按最近的情况逐条选择；全部选完再提交。"),
      progress,
    );
    if (data.followup)
      card.append(renderCalculationFollowup(data.followup, context));

    const form = el("form", "calculation-form scale-form-body");
    const initial = context.calculationDraft || data.input || {};
    const groups = [];
    items.forEach((item, index) => {
      const fieldset = el("fieldset", "scale-item");
      const legend = el("legend", "scale-item-label");
      legend.append(
        el("span", "scale-item-index", String(index + 1)),
        item.label,
      );
      fieldset.append(legend);
      const options = el("div", "scale-options");
      for (const option of item.options) {
        const label = el("label", "scale-option");
        const input = el("input");
        input.type = "radio";
        input.name = item.id;
        input.value = String(option.value);
        input.required = true;
        if (initial[item.id] === option.value) input.checked = true;
        input.addEventListener("invalid", () =>
          input.setCustomValidity("请选择一项。"),
        );
        label.append(input, el("span", "scale-option-text", option.label));
        options.append(label);
      }
      fieldset.append(options);
      form.append(fieldset);
      groups.push(fieldset);
    });

    const answers = () => {
      const output = {};
      for (const fieldset of groups) {
        const picked = fieldset.querySelector("input:checked");
        if (picked) output[picked.name] = Number(picked.value);
      }
      return output;
    };
    const refresh = () => {
      const answered = Object.keys(answers()).length;
      progress.textContent = `已选 ${answered} / ${items.length} 项`;
    };
    refresh();
    form.addEventListener("change", (event) => {
      event.target.setCustomValidity?.("");
      refresh();
      context.onCalculationDraft?.(answers());
    });

    const feedback = el("p", "calculation-feedback", data.feedback || "");
    feedback.setAttribute("role", "status");
    const actions = el("div", "calculation-form-actions");
    const submit = el("button", "primary-button", "提交并计算");
    submit.type = "submit";
    // Native constraint validation fires before the submit event, so a
    // handler on submit never runs when answers are missing. Catch the
    // click first and report how many items are still blank.
    submit.addEventListener("click", (event) => {
      const firstMissing = groups.find(
        (fieldset) => !fieldset.querySelector("input:checked"),
      );
      if (!firstMissing) return;
      event.preventDefault();
      const missing = groups.filter(
        (fieldset) => !fieldset.querySelector("input:checked"),
      ).length;
      feedback.textContent = `还有 ${missing} 项没有选择，请补齐后再提交。`;
      // Scroll to the gap instead of opening a native validation bubble:
      // the bubble obscures the card and blocks further clicks in some
      // browsers, and on a 25-item scale it can point far off screen.
      firstMissing.scrollIntoView({ block: "center" });
      firstMissing.querySelector("input")?.focus({ preventScroll: true });
    });
    const skip = button(
      "先跳过",
      () => {
        if (!context.isBusy?.()) context.onSkipCalculation?.(data.proposal_id);
      },
      "text-button",
    );
    actions.append(submit, skip);
    form.append(feedback, actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (context.isBusy?.()) return;
      if (!form.reportValidity()) {
        feedback.textContent = "还有条目没有选择，请补齐后再提交。";
        return;
      }
      context.onConfirmCalculation?.(data.proposal_id, answers());
    });
    if (!context.onConfirmCalculation)
      for (const field of form.elements) field.disabled = true;

    const details = el("details", "calculation-source");
    details.append(
      el("summary", "", "查看原话与计分范围"),
      el("blockquote", "calculation-evidence", data.source_quote),
      el(
        "p",
        "",
        `${data.name} · ${data.version}；${data.scope_note || "量表计分只反映本次作答，不构成诊断。"}`,
      ),
    );
    card.append(form, details);
    return card;
  }

  function renderScaleResult(data, context) {
    const card = el("article", "calculation-card scale-result");
    const patient = data.provenance.input_origin === "patient_confirmed_form";
    if (patient) card.classList.add("patient-calculation-result");
    const top = el("div", "calculation-heading");
    top.append(
      el(
        "p",
        data.subject_label ? "calculation-title" : "task-kicker",
        data.subject_label || "量表计分",
      ),
      el(
        "span",
        "calculation-status",
        `已计算 · ${inputStates[data.input_status]}`,
      ),
    );
    const score = el("div", "scale-score");
    score.append(
      el("span", "scale-score-value", String(data.value)),
      el("span", "scale-score-unit", `/ ${data.max_score} ${data.unit}`),
    );
    card.append(top, score);
    if (data.band?.label) {
      const chip = el("p", "scale-band", data.band.label);
      if (data.band.range)
        chip.append(el("span", "scale-band-range", ` ${data.band.range}`));
      card.append(chip);
    }
    card.append(el("p", "scale-disclaimer", data.disclaimer));

    const details = el("details", "scale-detail");
    details.append(el("summary", "", "逐项作答与计分"));
    const rows = el("dl", "calculation-inputs");
    for (const item of list(data.items)) {
      const row = el("div");
      row.append(
        el("dt", "", item.label),
        el("dd", "", `${item.answer_label} · ${item.score} 分`),
      );
      rows.append(row);
    }
    details.append(rows);
    card.append(details, el("p", "calculation-formula", data.formula));
    card.append(
      el(
        "p",
        "calculation-caution",
        patient
          ? "作答已核对；结果只反映本次作答，未写入病例。"
          : `${inputStates[data.input_status]} · ${qualityNames[data.quality]}。结果未写为临床事实。`,
      ),
    );
    if (list(data.limitations).length) {
      const limits = el("details", "calculation-limitations");
      limits.append(el("summary", "", "计算范围与限制"));
      for (const item of data.limitations) limits.append(el("p", "", item));
      card.append(limits);
    }
    if (data.references?.length) {
      const refs = el("details", "calculation-references");
      refs.append(el("summary", "", "量表来源"));
      for (const ref of data.references) {
        const link = el("a", "", ref.title);
        link.href = ref.url;
        link.rel = "noreferrer noopener";
        link.target = "_blank";
        refs.append(
          link,
          el("p", "", `${ref.section}（访问于 ${ref.accessed}）`),
        );
      }
      card.append(refs);
    }
    return card;
  }

  function formulaInputs(data) {
    const inputs = list(data.inputs);
    // Must match formula_engine.MAX_INPUTS. It sat at 8 while the server
    // accepted 20, so any formula with nine or more inputs — burn area,
    // EASI, PASI — threw here and rendered no card at all.
    if (!inputs.length || inputs.length > 20)
      throw new Error("Invalid formula inputs");
    const seen = new Set();
    return inputs.map((item) => {
      if (typeof item.id !== "string" || !item.id || seen.has(item.id))
        throw new Error("Invalid formula input id");
      seen.add(item.id);
      text(item.label);
      if (
        item.type !== "number" &&
        item.type !== "enum" &&
        item.type !== "date"
      )
        throw new Error("Invalid formula input type");
      if (item.unit !== undefined) text(item.unit);
      if (item.type === "date") {
        for (const key of ["min", "max"])
          if (item[key] !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(item[key]))
            throw new Error("Invalid formula date bound");
      } else {
        for (const key of ["min", "max", "step"])
          if (
            item[key] !== undefined &&
            !/^-?\d{1,6}(\.\d{1,4})?$/.test(item[key])
          )
            throw new Error("Invalid formula bound");
      }
      if (item.type === "enum") {
        const options = list(item.options);
        if (options.length < 2 || options.length > 12)
          throw new Error("Invalid formula options");
        options.forEach((option) => {
          text(option.value);
          text(option.label);
        });
      }
      return item;
    });
  }

  function validateFormulaDraft(data) {
    const inputs = formulaInputs(data);
    if (
      !data.input ||
      typeof data.input !== "object" ||
      Array.isArray(data.input)
    )
      throw new Error("Invalid formula input");
    const ids = new Set(inputs.map((item) => item.id));
    for (const [key, value] of Object.entries(data.input)) {
      if (!ids.has(key) || typeof value !== "string" || value.length > 12)
        throw new Error("Invalid formula value");
    }
    if (list(data.unit_options).length)
      throw new Error("Formula draft must not carry units");
    return inputs;
  }

  function renderFormulaForm(data, context) {
    const inputs = formulaInputs(data);
    const card = el("article", "patient-calculation-input formula-form");
    const symbol = el("div", "question-symbol");
    symbol.append(visualIcon("note"));
    card.append(
      symbol,
      el("p", "task-kicker", "填写数值"),
      el("h2", "question-title", data.name),
      el(
        "p",
        "scale-intro",
        "请按实际测量值填写；超出可计算范围的数值不会被计算。",
      ),
    );
    if (data.followup)
      card.append(renderCalculationFollowup(data.followup, context));

    const form = el("form", "calculation-form formula-form-body");
    const initial = context.calculationDraft || data.input || {};
    const fields = el("div", "formula-fields");
    for (const item of inputs) {
      const holder = el("label", "formula-field", item.label);
      if (item.type === "enum") {
        const select = el("select");
        select.name = item.id;
        select.required = true;
        select.append(new Option("请选择", ""));
        for (const option of item.options)
          select.append(new Option(option.label, option.value));
        select.value = initial[item.id] || "";
        holder.append(select);
      } else if (item.type === "date") {
        const input = el("input");
        input.name = item.id;
        input.type = "date";
        input.required = true;
        if (item.min) input.min = item.min;
        if (item.max) input.max = item.max;
        input.value = initial[item.id] || "";
        holder.append(input);
      } else {
        const input = el("input");
        input.name = item.id;
        input.type = "text";
        input.inputMode = "decimal";
        input.maxLength = 12;
        input.required = true;
        input.pattern = "-?[0-9]{1,6}(\\.[0-9]{1,4})?";
        if (item.min !== undefined) input.dataset.min = item.min;
        if (item.max !== undefined) input.dataset.max = item.max;
        input.value = initial[item.id] || "";
        input.placeholder = "填写数值";
        holder.append(input);
      }
      if (item.unit) holder.append(el("span", "formula-unit", item.unit));
      if (item.min !== undefined && item.max !== undefined)
        holder.append(
          el("span", "formula-range", `可计算范围 ${item.min}–${item.max}`),
        );
      fields.append(holder);
    }

    const values = () => {
      const output = {};
      for (const element of form.elements) {
        if (!element.name) continue;
        if (element.value !== "") output[element.name] = element.value;
      }
      return output;
    };
    const feedback = el("p", "calculation-feedback", data.feedback || "");
    feedback.setAttribute("role", "status");
    form.addEventListener("input", (event) => {
      event.target.setCustomValidity?.("");
      feedback.textContent = "";
      context.onCalculationDraft?.(values());
    });

    const actions = el("div", "calculation-form-actions");
    const submit = el("button", "primary-button", "核对后计算");
    submit.type = "submit";
    // Same reason as the scale form: native validation blocks the submit
    // event, so the in-card message has to be raised from the click.
    submit.addEventListener("click", (event) => {
      const empty = [...form.elements].filter(
        (field) => field.name && field.required && !field.value,
      );
      if (!empty.length) return;
      event.preventDefault();
      feedback.textContent = `还有 ${empty.length} 项没有填写，请补齐后再计算。`;
      empty[0].scrollIntoView({ block: "center" });
      empty[0].focus({ preventScroll: true });
    });
    const skip = button(
      "先跳过",
      () => {
        if (!context.isBusy?.()) context.onSkipCalculation?.(data.proposal_id);
      },
      "text-button",
    );
    actions.append(submit, skip);
    form.append(fields, feedback, actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (context.isBusy?.()) return;
      if (!form.reportValidity()) {
        feedback.textContent = "还有数值没有填写，请补齐后再计算。";
        return;
      }
      context.onConfirmCalculation?.(data.proposal_id, values());
    });
    if (!context.onConfirmCalculation)
      for (const field of form.elements) field.disabled = true;

    const details = el("details", "calculation-source");
    details.append(
      el("summary", "", "查看原话与计算范围"),
      el("blockquote", "calculation-evidence", data.source_quote),
      el(
        "p",
        "",
        `${data.name} · ${data.version}；${data.scope_note || "只做算术计算，不评价结果。"}`,
      ),
    );
    card.append(form, details);
    return card;
  }

  register(
    "calculation_input",
    (data) => {
      for (const key of [
        "proposal_id",
        "capability_id",
        "name",
        "version",
        "registry_sha256",
        "source_quote",
        "source_turn_id",
        "feedback",
      ])
        text(data[key]);
      const formKind = data.form_kind || "unit_conversion/v1";
      if (formKind === SCALE_FORM) {
        validateScaleDraft(data);
        if (data.subject_label !== undefined) text(data.subject_label);
        if (data.scope_note !== undefined) text(data.scope_note);
        return;
      }
      if (formKind === FORMULA_FORM) {
        validateFormulaDraft(data);
        if (data.subject_label !== undefined) text(data.subject_label);
        if (data.scope_note !== undefined) text(data.scope_note);
        return;
      }
      if (
        !data.input ||
        typeof data.input !== "object" ||
        Array.isArray(data.input)
      )
        throw new Error("Invalid input draft");
      for (const [key, value] of Object.entries(data.input)) {
        if (!["value", "from_unit", "to_unit"].includes(key))
          throw new Error("Unknown input");
        text(value);
      }
      const units = list(data.unit_options);
      if (data.followup !== undefined)
        validateCalculationFollowup(data.followup);
      if (!["unit_conversion/v1", "unit_conversion/v2"].includes(formKind))
        throw new Error("Unknown input form");
      if (data.subject_label !== undefined) text(data.subject_label);
      if (data.scope_note !== undefined) text(data.scope_note);
      if (
        formKind === "unit_conversion/v2" &&
        (!data.subject_label?.trim() || !data.scope_note?.trim())
      )
        throw new Error("Missing calculation subject or scope");
      if (
        (formKind === "unit_conversion/v1"
          ? units.length !== 5
          : !units.length || units.length > 16) ||
        new Set(units.map((item) => item.value)).size !== units.length
      )
        throw new Error("Invalid units");
      for (const unit of units) {
        text(unit.value);
        if (
          !unit.value ||
          unit.value.length > 32 ||
          (formKind === "unit_conversion/v1" &&
            !["kg", "g", "mg", "L", "mL"].includes(unit.value))
        )
          throw new Error("Unknown unit");
        text(unit.label);
      }
    },
    (data, context) => {
      if (data.form_kind === SCALE_FORM) return renderScaleForm(data, context);
      if (data.form_kind === FORMULA_FORM)
        return renderFormulaForm(data, context);
      const card = el("article", "patient-calculation-input");
      const symbol = el("div", "question-symbol");
      symbol.append(visualIcon("note"));
      card.append(
        symbol,
        el("p", "task-kicker", "先核对，再计算"),
        el(
          "h2",
          "question-title",
          ["value", "from_unit", "to_unit"].every((key) => data.input[key])
            ? "这三个输入，对吗？"
            : "补充这三个输入",
        ),
      );
      const form = el("form", "calculation-form");
      if (data.followup)
        card.append(renderCalculationFollowup(data.followup, context));
      if (data.form_kind === "unit_conversion/v2")
        card.prepend(el("p", "calculation-subject", data.subject_label));
      const fields = el("div", "conversion-fields");
      const initial = context.calculationDraft || data.input;
      const valueLabel = el("label", "conversion-value", "数值");
      const value = el("input");
      value.name = "value";
      value.type = "text";
      value.inputMode = "decimal";
      value.maxLength = 25;
      value.required = true;
      value.pattern = "[0-9]{1,12}(\\.[0-9]{1,12})?";
      value.value = initial.value || "";
      value.placeholder = "填写数值";
      value.addEventListener("invalid", () =>
        value.setCustomValidity(
          "请填写非负小数，整数和小数各最多12位，例如1.25。",
        ),
      );
      valueLabel.append(value);
      fields.append(valueLabel);
      for (const [key, label] of [
        ["from_unit", "原单位"],
        ["to_unit", "目标单位"],
      ]) {
        if (key === "to_unit") {
          const arrow = el("span", "conversion-arrow", "→");
          arrow.setAttribute("aria-hidden", "true");
          fields.append(arrow);
        }
        const holder = el("label", "", label),
          select = el("select");
        select.name = key;
        select.setAttribute("aria-label", label);
        select.required = true;
        select.addEventListener("invalid", () =>
          select.setCustomValidity("请选择单位。"),
        );
        select.append(new Option("请选择", ""));
        for (const unit of data.unit_options)
          select.append(new Option(unit.label, unit.value));
        select.value = initial[key] || "";
        holder.append(select);
        fields.append(holder);
      }
      const feedback = el("p", "calculation-feedback", data.feedback);
      feedback.setAttribute("role", "status");
      const values = () => Object.fromEntries(new FormData(form));
      const update = (event) => {
        event.target.setCustomValidity?.("");
        feedback.textContent = "";
        context.onCalculationDraft?.(values());
      };
      form.addEventListener("input", update);
      form.addEventListener("change", update);
      const actions = el("div", "calculation-form-actions");
      const submit = el("button", "primary-button", "核对后计算");
      submit.type = "submit";
      const skip = button(
        "先跳过",
        () => {
          if (!context.isBusy?.())
            context.onSkipCalculation?.(data.proposal_id);
        },
        "text-button",
      );
      actions.append(submit, skip);
      form.append(fields, feedback, actions);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!context.isBusy?.() && form.reportValidity())
          context.onConfirmCalculation?.(data.proposal_id, values());
      });
      if (!context.onConfirmCalculation)
        for (const field of form.elements) field.disabled = true;
      const details = el("details", "calculation-source");
      details.append(
        el("summary", "", "查看原话与计算范围"),
        el("blockquote", "calculation-evidence", data.source_quote),
        el(
          "p",
          "",
          `${data.name} · ${data.version}；${data.scope_note || "仅同量纲换算，不判断摄入量或用药剂量。"}`,
        ),
      );
      card.append(
        el("p", "conversion-hint", "可直接修改；没有的信息，请补充。"),
        form,
        details,
      );
      return card;
    },
  );
  return { renderScaleResult };
}
