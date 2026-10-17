// Local component implementations. Serialized data never supplies executable code.
import {
  renderQuestion,
  validateQuestion,
  renderBodyMap,
  visualIcon,
} from "./visual-question.js";
export const UI_VERSION = "medical-ui/v1";

const sourceNames = {
  patient: "患者自述",
  caregiver: "照护者自述",
  device: "设备直连",
  camera: "相机观察",
  system_inference: "系统推断",
};
const qualityNames = {
  not_assessed: "质量未评价",
  adequate: "质量可用",
  inadequate: "质量不足",
  not_done: "未执行",
};
const inputStates = { unconfirmed: "输入待核对", confirmed: "输入已核对" };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null)
    node.textContent =
      typeof text === "object" ? JSON.stringify(text) : String(text);
  return node;
}
function button(label, handler, className = "") {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
function text(value) {
  if (typeof value !== "string" || value.length > 16000)
    throw new Error("Invalid text");
  return value;
}
function list(value) {
  if (!Array.isArray(value) || value.length > 100)
    throw new Error("Invalid list");
  return value;
}
function validateProvenance(item) {
  text(item.evidence);
  if (
    !Object.hasOwn(sourceNames, item.source) ||
    !Object.hasOwn(qualityNames, item.quality)
  )
    throw new Error("Invalid report provenance");
  if (item.context !== undefined) {
    text(item.context.text);
    text(item.context.turn_id);
    if (
      item.context.scope !== "source_turn" ||
      !["patient", "caregiver"].includes(item.source) ||
      !item.evidence ||
      !item.context.text.includes(item.evidence)
    )
      throw new Error("Invalid report context");
  }
}
function sourceTurnContext(context, evidence) {
  const details = el(
    "details",
    "unknown-report-expanded summary-source-context",
  );
  const quote = el("blockquote", "unknown-report-quote");
  const position = context.text.indexOf(evidence);
  const unique =
    evidence &&
    position >= 0 &&
    position === context.text.lastIndexOf(evidence);
  if (unique) {
    const mark = el("mark", "source-evidence-match", evidence);
    quote.append(
      document.createTextNode(context.text.slice(0, position)),
      mark,
      document.createTextNode(context.text.slice(position + evidence.length)),
    );
    details.addEventListener("toggle", () => {
      if (details.open)
        requestAnimationFrame(() => {
          if (
            details.isConnected &&
            details.open &&
            mark.getClientRects().length
          )
            mark.scrollIntoView({
              block: "center",
              inline: "nearest",
              behavior: "instant",
            });
        });
    });
  } else quote.textContent = context.text;
  details.append(
    el("summary", "", "查看同轮原话"),
    el(
      "p",
      "",
      "含同轮其他信息，尚未全部归入此字段；请以各项最新记录为准。" +
        (unique ? "高亮处为当前引文。" : ""),
    ),
    quote,
  );
  return details;
}
function asset(value) {
  if (
    typeof value !== "string" ||
    !/^\/[a-z0-9-]+\.svg(?:#step-[1-9][0-9]?)?$/.test(value)
  )
    throw new Error("Invalid local asset");
  return value;
}

export function replyEnvelope(reply) {
  if (reply.presentation) return reply.presentation;
  // Old event logs remain readable through this bounded compatibility adapter.
  const id = reply.action_id || reply.question_id || "legacy";
  const blocks = [
    {
      id: `message:${id}`,
      type: "message",
      data: { text: reply.message, kind: reply.kind },
    },
  ];
  if (reply.task)
    blocks.push({ id: `task:${id}`, type: "self_check", data: reply.task });
  if (reply.summary)
    blocks.push(
      reply.kind === "emergency"
        ? {
            id: `notice:${id}`,
            type: "notice",
            data: {
              title: reply.summary.care_route,
              message: reply.message,
              items: reply.summary.triggers || [],
            },
          }
        : { id: `summary:${id}`, type: "summary", data: reply.summary },
    );
  return { schema_version: UI_VERSION, blocks };
}

export function calculationEnvelope(result) {
  if (result.presentation) return result.presentation;
  return {
    schema_version: UI_VERSION,
    blocks: [
      {
        id: `calculation:${result.execution_id || "legacy"}`,
        type: result.renderer,
        data: {
          ...result.data,
          inputs: Object.entries(result.input || {}).map(([key, value]) => ({
            key,
            label: key,
            value,
          })),
          input_status: result.input_status,
          quality: result.quality,
          provenance: result,
        },
      },
    ],
  };
}

export function createRenderer({ taskProgress = new Map() } = {}) {
  const registry = new Map();
  function register(type, validate, render) {
    if (registry.has(type)) throw new Error("Component already registered");
    registry.set(type, { validate, render });
  }
  function fallback() {
    const node = el("article", "structured-fallback");
    node.setAttribute("role", "status");
    node.append(
      el("strong", "", "这部分内容暂时无法显示"),
      el(
        "p",
        "",
        "展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。",
      ),
    );
    return node;
  }
  function blocks(envelope) {
    if (envelope?.schema_version !== UI_VERSION)
      return [{ id: "unsupported-version", type: "unsupported" }];
    try {
      return list(envelope.blocks);
    } catch {
      return [{ id: "invalid-envelope", type: "unsupported" }];
    }
  }
  function render(block, context = {}) {
    try {
      text(block.id);
      const component = registry.get(block.type);
      if (!component || !block.data || typeof block.data !== "object")
        return fallback();
      component.validate(block.data);
      const node = component.render(block.data, context);
      node.dataset.blockId = block.id;
      node.dataset.blockType = block.type;
      return node;
    } catch {
      return fallback();
    }
  }
  function renderEnvelope(envelope, context = {}) {
    const fragment = document.createDocumentFragment();
    for (const block of blocks(envelope))
      fragment.append(render(block, context));
    return fragment;
  }

  register("question", validateQuestion, renderQuestion);

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
      if (
        units.length !== 5 ||
        new Set(units.map((item) => item.value)).size !== 5
      )
        throw new Error("Invalid units");
      for (const unit of units) {
        if (!["kg", "g", "mg", "L", "mL"].includes(unit.value))
          throw new Error("Unknown unit");
        text(unit.label);
      }
    },
    (data, context) => {
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
          `${data.name} · ${data.version}；仅同量纲换算，不判断摄入量或用药剂量。`,
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

  register(
    "message",
    (data) => {
      text(data.text);
      text(data.kind);
    },
    (data) => {
      const row = el(
        "div",
        `message agent ${["urgent", "emergency"].includes(data.kind) ? data.kind : ""}`,
      );
      const avatar = el("span", "message-avatar");
      const image = el("img");
      image.src = "brand.svg";
      image.alt = "";
      avatar.append(image);
      row.append(avatar, el("div", "bubble", data.text));
      return row;
    },
  );

  register(
    "notice",
    (data) => {
      text(data.title);
      if (data.message !== undefined) text(data.message);
      list(data.items).forEach(text);
    },
    (data) => {
      const node = el("article", "structured-notice");
      node.setAttribute("role", "alert");
      const heading = el("div", "notice-heading");
      heading.append(visualIcon("shield"), el("h2", "", data.title));
      node.append(heading);
      if (data.message) {
        node.append(el("p", "notice-action", data.message));
        if (data.items.length) {
          const reasons = el("details", "notice-reasons");
          reasons.append(
            el("summary", "", "查看触发原因"),
            el("p", "", data.items.join("、")),
          );
          node.append(reasons);
        }
      } else node.append(el("p", "", data.items.join("、")));
      return node;
    },
  );

  register(
    "self_check",
    (data) => {
      text(data.id);
      text(data.title);
      text(data.purpose);
      asset(data.asset);
      const steps = list(data.steps);
      if (!steps.length) throw new Error("No steps");
      steps.forEach(text);
      list(data.equipment).forEach(text);
      list(data.stop_conditions).forEach(text);
      list(data.clinical_limits).forEach(text);
      if (data.feedback !== undefined) text(data.feedback);
      text(data.response_prompt);
      if (data.step_assets) {
        if (list(data.step_assets).length !== steps.length)
          throw new Error("Mismatched steps");
        data.step_assets.forEach(asset);
      }
    },
    (data, context) => {
      const card = el("article", "task-card structured-task");
      const header = el("div", "task-header");
      const title = el("div");
      title.append(
        el(
          "div",
          "task-kicker",
          context.preview ? "图示任务 · 仅预览" : "定向观察 · 可以跳过",
        ),
        el("h3", "", data.title),
      );
      header.append(
        title,
        el(
          "span",
          "review-chip",
          data.review_status === "prototype_requires_clinical_review"
            ? "待临床审核"
            : "请查看任务说明",
        ),
      );
      card.append(header);
      if (data.feedback) {
        const feedback = el("p", "task-feedback", data.feedback);
        feedback.setAttribute("role", "alert");
        card.append(feedback);
      }
      if (data.equipment.length)
        card.append(
          el("p", "task-equipment", `准备：${data.equipment.join("、")}`),
        );
      const stage = el("div", "task-stage");
      const figure = el("figure", "step-figure");
      const image = el("img", "task-image");
      const caption = el("figcaption");
      figure.append(image, caption);
      const view = el("div", "step-view");
      const progress = el("div", "step-progress");
      const instruction = el("p", "step-text");
      instruction.setAttribute("aria-live", "polite");
      const controls = el("div", "step-controls");
      const progressKey = `${context.sessionId || "preview"}:${data.id}:${data.provider?.version || "1"}`;
      let step = Math.min(
        taskProgress.get(progressKey) || 0,
        data.steps.length - 1,
      );
      const previous = button("上一步", () => {
        step--;
        update();
      });
      const next = button("下一步", () => {
        if (step < data.steps.length - 1) {
          step++;
          update();
        } else context.onAnswer?.();
      });
      controls.append(previous, next);
      view.append(progress, instruction, controls);
      stage.append(figure, view);
      if (data.id === "body_location" && !context.preview && context.onChoose) {
        stage.classList.add("location-stage");
        figure.replaceChildren(renderBodyMap(data, context));
      }
      card.append(stage);
      function update() {
        taskProgress.set(progressKey, step);
        progress.replaceChildren(
          el("span", "", `${step + 1} / ${data.steps.length}`),
        );
        data.steps.forEach((_, index) =>
          progress.append(el("i", index === step ? "current" : "")),
        );
        instruction.textContent = data.steps[step];
        image.src = data.step_assets?.[step] || data.asset;
        image.alt = `${data.title}：第 ${step + 1} 步，${data.steps[step]}`;
        caption.textContent = `步骤 ${step + 1} · ${data.step_assets ? "当前步骤示意" : "请结合图示阅读当前步骤"}`;
        previous.disabled = step === 0;
        next.textContent =
          step === data.steps.length - 1
            ? context.preview
              ? "最后一步"
              : "填写结果"
            : "下一步";
        next.disabled =
          step === data.steps.length - 1 &&
          (context.preview || !context.onAnswer);
      }
      update();
      const details = el("details", "task-details");
      details.append(
        el("summary", "", "为什么观察 · 使用限制"),
        el("p", "task-purpose", data.purpose),
      );
      data.clinical_limits.forEach((item) => details.append(el("p", "", item)));
      card.append(
        el("p", "task-stop", `停止 / 跳过：${data.stop_conditions.join("、")}`),
        details,
        el(
          "p",
          "task-prompt",
          context.preview
            ? "这是素材预览，不会记录结果或改变问诊进度。"
            : data.response_prompt,
        ),
      );
      return card;
    },
  );

  register(
    "summary",
    (data) => {
      list(data.known).forEach((item) => {
        text(item.key);
        text(item.label);
        if (item.value === undefined || item.value === null)
          throw new Error("Missing value");
        if (item.evidence !== undefined || item.context !== undefined)
          validateProvenance(item);
      });
      list(data.unknown).forEach(text);
      if (data.unknown_details !== undefined) {
        const seen = new Set();
        list(data.unknown_details).forEach((item) => {
          text(item.key);
          text(item.label);
          validateProvenance(item);
          if (seen.has(item.key) || !data.unknown.includes(item.label))
            throw new Error("Invalid unresolved report");
          seen.add(item.key);
        });
      }
      text(data.care_route);
      list(data.return_now_if).forEach(text);
    },
    (data, context) => {
      const card = el("article", "summary-card");
      card.append(
        el(
          "p",
          "task-kicker",
          context.readOnly ? "会谈记录 · 可回看" : "核对记录 · 可以更正",
        ),
        el("h3", "", "本次问诊摘要"),
      );
      card.append(
        el("p", "summary-intro", "请核对已记录的信息。“未知”表示尚未确认。"),
      );
      const groups = [
        { title: "当前不适", icon: "activity", items: [] },
        { title: "观察记录", icon: "person", items: [] },
        { title: "用药与背景", icon: "note", items: [] },
      ];
      const highlights = data.known.filter((item) =>
        ["age_years", "temperature_c", "severity"].includes(item.key),
      );
      const overview = el("div", "summary-grid summary-overview");
      function factRow(item, highlight = false) {
        const row = el("div", "summary-item");
        row.dataset.factKey = item.key;
        const value = el("div");
        const source = [
          sourceNames[item.source] || "来源未标注",
          qualityNames[item.quality],
        ]
          .filter(Boolean)
          .join(" · ");
        value.append(
          el(
            "strong",
            "",
            item.key === "severity" && /^\d+$/.test(String(item.value))
              ? `${item.value} / 10`
              : item.value,
          ),
        );
        if (item.key === "temperature_c")
          value.append(el("div", "measurement-source", source));
        if (item.key !== "temperature_c" || item.evidence || item.context) {
          const provenance = el("details", "summary-provenance");
          const reported = ["patient", "caregiver"].includes(item.source);
          provenance.append(
            el(
              "summary",
              "",
              item.evidence ? (reported ? "原话与来源" : "依据与来源") : "来源",
            ),
          );
          if (item.key !== "temperature_c")
            provenance.append(el("div", "fact-source", source));
          if (item.evidence) {
            const quote = el(
              "blockquote",
              "unknown-report-quote summary-evidence",
              item.evidence,
            );
            quote.setAttribute("aria-label", `${item.label}：记录引文`);
            provenance.append(quote);
          }
          if (item.context)
            provenance.append(sourceTurnContext(item.context, item.evidence));
          value.append(provenance);
        }
        const label = el("span", "", item.label);
        if (highlight)
          label.prepend(
            visualIcon(
              item.key === "age_years"
                ? "person"
                : item.key === "severity"
                  ? "activity"
                  : "note",
            ),
          );
        row.append(label, value);
        if (!context.readOnly && context.canEdit?.(item.key))
          row.append(
            button(
              "更正",
              () => context.onCorrect?.(item.key),
              "fact-edit summary-edit",
            ),
          );
        return row;
      }
      highlights.forEach((item) => overview.append(factRow(item, true)));
      if (highlights.length) card.append(overview);
      if (data.unknown.length) {
        const unknown = el("div", "summary-unknown");
        unknown.append(el("strong", "", "仍待确认"));
        const chips = el("div", "unknown-items");
        const reports = data.unknown_details || [];
        data.unknown.forEach((label) => {
          const report = reports.find((item) => item.label === label);
          if (!report) {
            chips.append(el("span", "", label));
            return;
          }
          const item = el("section", "unknown-report");
          item.dataset.factKey = report.key;
          const heading = el("div", "unknown-report-heading");
          heading.append(el("strong", "", label));
          if (!context.readOnly && context.canEdit?.(report.key))
            heading.append(
              button(
                "补充 / 更正",
                () => context.onCorrect?.(report.key),
                "fact-edit summary-edit",
              ),
            );
          item.append(heading);
          if (report.evidence) {
            const quote = el(
              "blockquote",
              "unknown-report-quote",
              report.evidence,
            );
            quote.setAttribute("aria-label", `${label}：原话，待确认`);
            if (report.evidence.length > 160) {
              const details = el("details", "unknown-report-expanded");
              details.append(el("summary", "", "查看已提供的原话"), quote);
              item.append(details);
            } else item.append(quote);
          }
          item.append(
            el(
              "small",
              "unknown-report-source",
              `${sourceNames[report.source]} · ${report.quality === "not_done" ? "未执行 / 已跳过" : "尚未确认"}`,
            ),
          );
          if (report.context) {
            item.append(sourceTurnContext(report.context, report.evidence));
          }
          chips.append(item);
        });
        unknown.append(chips);
        card.append(unknown);
      }
      for (const item of data.known.filter(
        (item) => !highlights.includes(item),
      )) {
        const index = ["temperature_c", "symptom_location"].includes(item.key)
          ? 1
          : [
                "consult_for",
                "age_years",
                "medications_taken",
                "allergies",
                "relevant_history",
                "patient_goal",
              ].includes(item.key)
            ? 2
            : 0;
        groups[index].items.push(item);
      }
      for (const group of groups.filter((group) => group.items.length)) {
        const section = el("section", "summary-group");
        const heading = el("h4");
        heading.append(visualIcon(group.icon), el("span", "", group.title));
        section.append(heading);
        const grid = el("div", "summary-grid");
        for (const item of group.items) {
          grid.append(factRow(item));
        }
        section.append(grid);
        card.append(section);
      }
      card.append(
        el(
          "div",
          `summary-route ${data.risk_level === "urgent" ? "urgent" : ""}`,
          data.care_route,
        ),
      );
      if (data.return_now_if.length) {
        const details = el("details");
        details.append(
          el("summary", "", "哪些变化需要立即求助"),
          el("p", "", data.return_now_if.join("；")),
        );
        card.append(details);
      }
      return card;
    },
  );

  register(
    "calculation",
    (data) => {
      if (
        !["string", "number"].includes(typeof data.value) ||
        (typeof data.value === "number" && !Number.isFinite(data.value))
      )
        throw new Error("Invalid result");
      text(data.unit);
      text(data.formula);
      text(data.method);
      list(data.limitations).forEach(text);
      list(data.inputs).forEach((item) => {
        text(item.label);
        if (item.value === undefined) throw new Error("Missing input");
      });
      if (!data.provenance) throw new Error("Missing provenance");
      for (const key of [
        "capability_id",
        "version",
        "registry_sha256",
        "input_origin",
      ])
        text(data.provenance[key]);
      if (data.provenance.evidence !== undefined) {
        text(data.provenance.evidence.quote);
        text(data.provenance.evidence.source_turn_id);
        if (data.provenance.evidence.scope !== "quantity_and_source_unit_only")
          throw new Error("Unsupported evidence scope");
        text(data.provenance.implementation_sha256);
        text(data.provenance.execution_id);
      }
      if (data.provenance.confirmation !== undefined) {
        for (const key of [
          "proposal_id",
          "source_quote",
          "source_turn_id",
          "confirmation_turn_id",
        ])
          text(data.provenance.confirmation[key]);
        if (
          data.provenance.input_origin !== "patient_confirmed_form" ||
          data.input_status !== "confirmed"
        )
          throw new Error("Invalid confirmation");
        for (const key of ["original_input", "confirmed_input"]) {
          const values = data.provenance.confirmation[key];
          if (!values || typeof values !== "object" || Array.isArray(values))
            throw new Error("Invalid confirmed input");
        }
      }
      if (
        !Object.hasOwn(inputStates, data.input_status) ||
        !Object.hasOwn(qualityNames, data.quality)
      )
        throw new Error("Invalid quality");
    },
    (data, context) => {
      const card = el("article", "calculation-card");
      const patient = data.provenance.input_origin === "patient_confirmed_form";
      if (patient) card.classList.add("patient-calculation-result");
      const top = el("div", "calculation-heading");
      top.append(
        el("p", "task-kicker", "显式计算"),
        el(
          "span",
          "calculation-status",
          `已计算 · ${inputStates[data.input_status]}`,
        ),
      );
      card.append(top, el("h3", "", `${data.value} ${data.unit}`));
      const inputs = el("dl", "calculation-inputs");
      for (const item of data.inputs) {
        const row = el("div");
        row.append(el("dt", "", item.label), el("dd", "", item.value));
        inputs.append(row);
      }
      card.append(inputs, el("p", "calculation-formula", data.formula));
      if (!patient) card.append(el("p", "", data.method));
      card.append(
        el(
          "p",
          "calculation-caution",
          patient
            ? "已核对计算参数；实际测量质量未评价。"
            : `${inputStates[data.input_status]} · ${qualityNames[data.quality]}。结果未写为临床事实。`,
        ),
      );
      const details = el("details");
      details.append(
        el("summary", "", "执行依据与限制"),
        el(
          "p",
          "",
          `${data.provenance.capability_id} @ ${data.provenance.version}`,
        ),
        el(
          "p",
          "",
          data.provenance.input_origin === "developer_preview"
            ? "来源：研发独立输入"
            : patient
              ? "来源：用户核对的输入卡"
              : "来源：模型提议输入",
        ),
        el("p", "", `能力组合：${data.provenance.registry_sha256}`),
      );
      if (patient) {
        details.append(
          el("p", "", data.method),
          el("p", "", "最初的换算请求："),
          el(
            "blockquote",
            "calculation-evidence",
            data.provenance.confirmation.source_quote,
          ),
        );
        const confirmation = data.provenance.confirmation;
        if (
          Object.keys(confirmation.original_input).length !==
            Object.keys(confirmation.confirmed_input).length ||
          Object.entries(confirmation.original_input).some(
            ([key, value]) => confirmation.confirmed_input[key] !== value,
          )
        )
          details.append(
            el("p", "", "核对时补充或修改了输入；结果按上方最终参数计算。"),
          );
      }
      if (data.provenance.evidence) {
        details.append(
          el(
            "p",
            "",
            "原文数值与原单位已核对；目标单位仍为模型提议，实际测量尚未确认。",
          ),
          el(
            "blockquote",
            "calculation-evidence",
            data.provenance.evidence.quote,
          ),
          el("p", "", `原文回合：${data.provenance.evidence.source_turn_id}`),
          el("p", "", `实现摘要：${data.provenance.implementation_sha256}`),
          el("p", "", `回执：${data.provenance.execution_id}`),
        );
      }
      data.limitations.forEach((item) => details.append(el("p", "", item)));
      card.append(details);
      if (context.onContinueCalculation)
        card.append(
          button(
            "继续问诊",
            () => {
              if (!context.isBusy?.()) context.onContinueCalculation();
            },
            "primary-button calculation-continue",
          ),
        );
      return card;
    },
  );

  return { register, render, renderEnvelope, blocks };
}
