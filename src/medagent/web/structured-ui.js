// Local component implementations. Serialized data never supplies executable code.
import {
  renderQuestion,
  validateQuestion,
  renderBodyMap,
  visualIcon,
} from "./visual-question.js";
import {
  renderLocationAnswer,
  validateLocationAnswerSpec,
  renderLocationChips,
} from "./location-answer-ui.js";
export const UI_VERSION = "medical-ui/v1";
let illustrationSequence = 0;
// Compact captions only apply to this exact, repository-owned instruction set.
// Other capability versions retain their complete supplied instructions.
const temperatureGuide = [
  ["先阅读体温计说明书；擦干腋窝。", "擦干腋窝", "先阅读体温计说明书", "准备"],
  [
    "将探头放在腋窝中央，手臂贴紧身体。",
    "探头放在腋窝中央",
    "手臂贴紧身体",
    "放置",
  ],
  [
    "按设备说明等待测量完成，记录数值、腋温和测量时间。",
    "等待完成，再记录",
    "按设备说明等待 · 记录数值、腋温和时间",
    "读数",
  ],
];

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
function validateCalculationFollowup(data) {
  for (const key of [
    "id",
    "capability_id",
    "text",
    "source_turn_id",
    "outcome",
  ])
    text(data[key]);
  if (
    !data.id ||
    !data.source_turn_id ||
    data.text.length > 2000 ||
    !["awaiting_input", "completed", "skipped", "cancelled"].includes(
      data.outcome,
    ) ||
    !["pending", "forwarded", "unprocessed"].includes(data.status) ||
    typeof data.can_process !== "boolean" ||
    typeof data.processing !== "boolean"
  )
    throw new Error("Invalid calculation followup");
  if (data.can_process && (data.status !== "pending" || data.processing))
    throw new Error("Invalid followup action");
}
function renderCalculationFollowup(data, context) {
  const section = el("section", "interaction-followup");
  section.dataset.status = data.status;
  const details = el("details");
  details.append(
    el(
      "summary",
      "",
      data.status === "pending"
        ? "还有同轮补充"
        : data.status === "forwarded"
          ? "补充已带入问诊"
          : "补充暂未处理",
    ),
    el("blockquote", "calculation-evidence", data.text),
  );
  section.append(details);
  if (
    data.can_process &&
    context.onProcessCalculationFollowup &&
    !context.readOnly
  ) {
    const action = button(
      "处理补充",
      () => {
        if (!context.isBusy?.()) context.onProcessCalculationFollowup(data.id);
      },
      "text-button followup-process",
    );
    action.dataset.actionId = context.actionId || "";
    section.append(action);
  }
  return section;
}
function validateObservations(data) {
  if (data.supplement !== undefined) {
    text(data.supplement.text);
    if (
      !["pending", "forwarded", "unprocessed"].includes(data.supplement.status)
    )
      throw new Error("Invalid supplement status");
  }
  if (
    data.task_id !== "axillary_temperature" ||
    data.key !== "temperature_c" ||
    data.unit !== "℃"
  )
    throw new Error("Unsupported observation");
  text(data.title);
  if (
    typeof data.can_repeat !== "boolean" ||
    !Number.isSafeInteger(data.total) ||
    data.total < 1
  )
    throw new Error("Invalid observation coverage");
  const entries = list(data.entries),
    ids = new Set();
  if (
    !entries.length ||
    entries.length > 8 ||
    data.total < entries.length ||
    entries.filter((e) => e.current === true).length !== 1
  )
    throw new Error("Invalid observation history");
  entries.forEach((entry) => {
    text(entry.id);
    text(entry.evidence);
    text(entry.recorded_at);
    if (
      ids.has(entry.id) ||
      !Number.isFinite(Date.parse(entry.recorded_at)) ||
      typeof entry.current !== "boolean" ||
      !["recorded", "unconfirmed", "skipped", "cancelled"].includes(
        entry.status,
      ) ||
      !Object.hasOwn(sourceNames, entry.source) ||
      !Object.hasOwn(qualityNames, entry.quality) ||
      (entry.status === "recorded"
        ? !Number.isFinite(entry.value) || entry.value < 34 || entry.value >= 44
        : entry.value !== null)
    )
      throw new Error("Invalid observation entry");
    ids.add(entry.id);
  });
  if (data.latest_status !== entries.at(-1).status)
    throw new Error("Invalid latest status");
}
function renderObservations(data, context) {
  const card = el("section", "observation-card");
  const compact = context.compactObservation === true;
  if (compact) card.classList.add("observation-compact");
  card.dataset.taskId = data.task_id;
  const current = data.entries.find((e) => e.current);
  const failed = ["skipped", "unconfirmed", "cancelled"].includes(
    data.latest_status,
  );
  const header = el("div", "observation-header");
  const reading = el(
    "strong",
    "observation-value",
    current.status === "recorded"
      ? `${current.value} ${data.unit}`
      : "尚无确认读数",
  );
  if (compact) {
    const metric = el("div", "observation-compact-metric");
    metric.append(el("span", "", "腋温"), reading);
    header.append(metric);
  } else header.append(el("span", "", data.title));
  if (data.can_repeat && !context.readOnly) {
    const repeat = button(
      "再测一次",
      () => context.onRepeat?.(data.task_id),
      "observation-repeat",
    );
    repeat.disabled = !context.canRepeat?.(data.task_id);
    header.append(repeat);
  }
  card.append(
    header,
    el(
      "span",
      "observation-current-label",
      failed ? "上次记录 · 本次未完成" : "最新记录",
    ),
  );
  if (!compact) card.append(reading);
  if (!compact)
    card.append(
      el(
        "p",
        "measurement-source",
        `${sourceNames[current.source]} · ${qualityNames[current.quality]}`,
      ),
    );
  const entries = el("ol", "observation-history");
  for (const entry of [...data.entries].reverse()) {
    const row = el("li", entry.current ? "current" : "");
    row.dataset.observationStatus = entry.status;
    const time = el(
      "time",
      "",
      new Date(entry.recorded_at).toLocaleString("zh-CN", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
    );
    time.dateTime = entry.recorded_at;
    const status = {
      recorded: `${entry.value} ${data.unit}`,
      skipped: "本次跳过",
      unconfirmed: "读数未确认",
      cancelled: "本次已取消",
    }[entry.status];
    row.append(
      time,
      el("strong", "", status),
      el("span", "observation-status", entry.current ? "保留记录" : ""),
    );
    if (entry.evidence) {
      const source = el("details", "observation-evidence");
      source.append(
        el("summary", "", "原话"),
        el("blockquote", "", entry.evidence),
      );
      row.append(source);
    }
    entries.append(row);
  }
  const history = el("details", "observation-details");
  history.append(
    el(
      "summary",
      "",
      `查看记录 · ${data.total}条${data.total > data.entries.length ? `（展示${data.entries.length}条，含保留读数）` : ""}`,
    ),
    entries,
    el(
      "p",
      "measurement-source",
      `${sourceNames[current.source]} · ${qualityNames[current.quality]}`,
    ),
    el(
      "p",
      "measurement-source",
      "时间为系统记录时间；手工填入的读数不代表设备直连或测量质量已验证。",
    ),
  );
  card.append(history);
  if (data.supplement) {
    const supplement = el("details", "observation-supplement");
    supplement.append(
      el(
        "summary",
        "",
        {
          pending: "补充信息 · 待重试",
          forwarded: "补充原话 · 已带入本轮问诊",
          unprocessed: "补充原话 · 尚未结构化",
        }[data.supplement.status],
      ),
      el("blockquote", "", data.supplement.text),
    );
    if (compact) {
      history.querySelector(":scope > summary").textContent =
        `记录与补充 · ${data.total}条${data.total > data.entries.length ? `（展示${data.entries.length}条）` : ""}`;
      history.append(supplement);
    } else card.append(supplement);
  }
  return card;
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
  if (reply.observation_result)
    blocks.push({
      id: `observation:${id}`,
      type: "observation_series",
      data: reply.observation_result,
    });
  if (reply.conclusion)
    blocks.push({
      id: `conclusion:${id}`,
      type: "conclusion",
      data: reply.conclusion,
    });
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
              level: "emergency",
              action_label: "现在拨打 120",
              action_href: "tel:120",
            },
          }
        : { id: `summary:${id}`, type: "summary", data: reply.summary },
    );
  if (reply.follow_up?.kind)
    blocks.push({
      id: `followup:${id}`,
      type: "follow_up",
      data: reply.follow_up,
    });
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
      if (data.followup !== undefined)
        validateCalculationFollowup(data.followup);
      const formKind = data.form_kind || "unit_conversion/v1";
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
      image.src = "brand.png";
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
      if (data.action_label !== undefined) text(data.action_label);
      if (data.action_href !== undefined && data.action_href !== "tel:120")
        throw new Error("Invalid emergency action");
      if (Boolean(data.action_label) !== Boolean(data.action_href))
        throw new Error("Incomplete emergency action");
      if (
        data.level !== undefined &&
        !["emergency", "urgent", "routine"].includes(data.level)
      )
        throw new Error("Invalid notice level");
    },
    (data) => {
      const level = data.level || "emergency";
      const node = el("article", `structured-notice level-${level}`);
      node.setAttribute("role", "alert");
      node.dataset.level = level;
      node.append(
        el(
          "p",
          "notice-level",
          level === "emergency" ? "立即行动" : "需要尽快处理",
        ),
      );
      const heading = el("div", "notice-heading");
      heading.append(visualIcon("shield"), el("h2", "", data.title));
      node.append(heading);
      if (data.action_label) {
        const action = el("a", "notice-cta", data.action_label);
        action.href = data.action_href;
        action.setAttribute("aria-label", "拨打急救电话 120");
        node.append(action);
      }
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

  register("observation_series", validateObservations, renderObservations);
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
      if (data.interaction_id !== undefined) {
        text(data.interaction_id);
        if (!data.interaction_id || data.interaction_id.length > 240)
          throw new Error("Invalid task interaction");
      }
      if (data.feedback !== undefined) text(data.feedback);
      if (data.clarification !== undefined) {
        text(data.clarification);
        if (
          data.id !== "body_location" ||
          !data.clarification.trim() ||
          data.clarification.length > 80
        )
          throw new Error("Invalid location clarification");
      }
      if (data.answer_spec !== undefined) {
        if (data.id !== "body_location")
          throw new Error("Unknown location form task");
        validateLocationAnswerSpec(data.answer_spec);
      }
      if (data.result_proposal !== undefined) {
        const proposal = data.result_proposal;
        text(proposal.evidence);
        text(proposal.choice);
        if (
          data.id !== "body_location" ||
          !["head", "chest", "abdomen", "back", "limbs", "other"].includes(
            proposal.option,
          ) ||
          !proposal.evidence ||
          proposal.evidence.length > 1000 ||
          !/^confirm_self_check:[0-9a-f-]{36}$/.test(proposal.choice)
        )
          throw new Error("Invalid location proposal");
      }
      if (data.repeat !== undefined) {
        text(data.repeat.attempt_id);
        const previous = data.repeat.previous;
        if (
          !previous ||
          previous.key !== "temperature_c" ||
          !["present", "unknown"].includes(previous.assertion) ||
          (previous.assertion === "present" &&
            (!Number.isFinite(previous.value) ||
              previous.value < 34 ||
              previous.value >= 44))
        )
          throw new Error("Invalid previous reading");
        const candidate = data.repeat.candidate;
        if (candidate) {
          text(candidate.evidence);
          if (
            !Number.isFinite(candidate.value) ||
            candidate.value < 34 ||
            candidate.value >= 44 ||
            candidate.choice !== `confirm_observation:${data.repeat.attempt_id}`
          )
            throw new Error("Invalid reading candidate");
        }
      }
      text(data.response_prompt);
      if (data.step_assets) {
        if (list(data.step_assets).length !== steps.length)
          throw new Error("Mismatched steps");
        data.step_assets.forEach(asset);
      }
    },
    (data, context) => {
      const card = el("article", "task-card structured-task");
      const compactGuide =
        data.id === "axillary_temperature" &&
        data.steps.length === temperatureGuide.length &&
        temperatureGuide.every(
          (item, index) => data.steps[index] === item[0],
        ) &&
        data.step_assets?.every(
          (value, index) =>
            value === `/neutral-temperature.svg#step-${index + 1}`,
        ) &&
        data.step_assets.length === temperatureGuide.length;
      if (compactGuide) card.classList.add("illustrated-task");
      const header = el("div", "task-header");
      const title = el("div");
      title.append(
        el(
          "div",
          "task-kicker",
          context.preview ? "图示任务 · 仅预览" : "定向观察 · 可以跳过",
        ),
        el("h3", "", data.clarification || data.title),
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
      if (data.repeat) {
        const previous = data.repeat.previous;
        card.append(
          el(
            "p",
            "observation-previous",
            `上次记录：${previous.assertion === "present" ? `${previous.value} ℃` : "读数未确认"} · 将保留`,
          ),
        );
        if (data.repeat.candidate) {
          const candidate = data.repeat.candidate;
          const proposal = el("div", "observation-proposal");
          proposal.append(
            el("span", "", "你刚才报告的腋温"),
            el("strong", "observation-value", `${candidate.value} ℃`),
          );
          const quote = el("details");
          quote.append(
            el("summary", "", "查看原话"),
            el("blockquote", "", candidate.evidence),
          );
          proposal.append(quote);
          if (!context.preview && context.onChoose)
            proposal.append(
              button(
                `核对并记录 ${candidate.value}℃`,
                () =>
                  context.onChoose(
                    `核对并记录 ${candidate.value}℃`,
                    candidate.choice,
                  ),
                "primary observation-confirm",
              ),
            );
          proposal.append(
            el(
              "p",
              "measurement-source",
              "读数不对可直接重新填写；尚未写入新记录。",
            ),
          );
          card.append(proposal);
        }
      }
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
      const fullInstruction = el("details", "step-full-instruction");
      const originalInstruction = el("ol", "step-full-list");
      data.steps.forEach((item) =>
        originalInstruction.append(el("li", "", item)),
      );
      fullInstruction.append(
        el("summary", "", "完整操作说明"),
        originalInstruction,
      );
      if (compactGuide) caption.classList.add("sr-only");
      figure.append(image, caption);
      if (data.id !== "body_location" || context.preview) {
        const viewer = el("dialog", "illustration-dialog");
        const viewerId = `illustration-title-${++illustrationSequence}`;
        const heading = el("h2");
        heading.id = viewerId;
        viewer.setAttribute("aria-labelledby", viewerId);
        const close = button(
          "关闭大图",
          () => viewer.close(),
          "secondary-button",
        );
        const large = el("img", "illustration-large");
        const description = el("p", "illustration-instruction");
        const zoom = button(
          "放大图示",
          () => {
            heading.textContent = `${data.title} · 步骤 ${step + 1}`;
            large.src = image.src;
            large.alt = image.alt;
            description.textContent = data.steps[step];
            viewer.showModal();
            close.focus();
          },
          "illustration-zoom",
        );
        zoom.setAttribute("aria-haspopup", "dialog");
        zoom.setAttribute("aria-label", "放大图示");
        // Make the complete illustration the touch target. A text button over
        // a small figure hides the very gesture the patient needs to see.
        zoom.replaceChildren(image, visualIcon("expand"));
        viewer.append(heading, description, large, close);
        figure.append(zoom, viewer);
        image.addEventListener("error", () => {
          image.hidden = true;
          zoom.hidden = true;
          caption.textContent =
            "图示暂时无法加载，请阅读右侧或下方的分步说明。";
          caption.setAttribute("role", "alert");
          caption.classList.remove("sr-only");
          if (compactGuide) fullInstruction.open = true;
        });
        image.addEventListener("load", () => {
          image.hidden = false;
          zoom.hidden = false;
          caption.removeAttribute("role");
          if (compactGuide) caption.classList.add("sr-only");
          caption.textContent = `步骤 ${step + 1} · ${data.step_assets ? "当前步骤示意" : "请结合图示阅读当前步骤"}`;
        });
      }
      const view = el("div", "step-view");
      const progress = el("div", "step-progress");
      const instruction = el("p", "step-text");
      instruction.setAttribute("aria-live", "polite");
      const cue = el("p", "step-cue");
      const controls = el("div", "step-controls");
      const progressKey = `${context.sessionId || "preview"}:${data.id}:${data.provider?.version || "1"}:${data.interaction_id || data.repeat?.attempt_id || context.actionId || "preview"}`;
      let step = Math.min(
        taskProgress.get(progressKey) || 0,
        data.steps.length - 1,
      );
      const previous = button("上一步", () => {
        if (!context.preview && (context.readOnly || context.isBusy?.()))
          return;
        step--;
        update({ focusInstruction: true });
      });
      const next = button("下一步", () => {
        if (!context.preview && (context.readOnly || context.isBusy?.()))
          return;
        if (step < data.steps.length - 1) {
          step++;
          update({ focusInstruction: true });
        } else context.onAnswer?.();
      });
      controls.append(previous, next);
      view.append(progress, instruction, controls);
      if (compactGuide) {
        instruction.after(cue);
      }
      stage.append(figure, view);
      let stepSummary;
      if (data.id === "body_location" && data.answer_spec && !context.preview) {
        stage.classList.add("location-stage");
        stage.replaceChildren(renderLocationAnswer(data, context));
      } else if (
        data.id === "body_location" &&
        !context.preview &&
        context.onChoose
      ) {
        stage.classList.add("location-stage");
        figure.replaceChildren(renderBodyMap(data, context));
        const guide = el("details", "location-step-guide");
        stepSummary = el("summary");
        guide.append(stepSummary, view);
        stage.replaceChildren(figure, guide);
      }
      card.append(stage);
      function update({ focusInstruction = false } = {}) {
        step = Math.max(0, Math.min(step, data.steps.length - 1));
        if (compactGuide) card.dataset.guideStep = String(step + 1);
        taskProgress.set(progressKey, step);
        progress.replaceChildren(
          el("span", "", `${step + 1} / ${data.steps.length}`),
        );
        if (compactGuide) {
          const track = el("ol", "step-track");
          track.setAttribute("aria-label", "测温步骤");
          temperatureGuide.forEach((item, index) => {
            const point = el(
              "li",
              index === step ? "current" : index < step ? "visited" : "",
            );
            if (index === step) point.setAttribute("aria-current", "step");
            point.append(
              el("b", "", String(index + 1)),
              el("span", "", item[3]),
            );
            track.append(point);
          });
          progress.append(track);
        } else
          data.steps.forEach((_, index) =>
            progress.append(el("i", index === step ? "current" : "")),
          );
        if (stepSummary)
          stepSummary.textContent = `分步说明 · ${step + 1} / ${data.steps.length}`;
        instruction.textContent = compactGuide
          ? temperatureGuide[step][1]
          : data.steps[step];
        cue.textContent = compactGuide ? temperatureGuide[step][2] : "";
        image.src = data.step_assets?.[step] || data.asset;
        image.alt = `${data.title}：第 ${step + 1} 步，${data.steps[step]}`;
        caption.textContent = `步骤 ${step + 1} · ${data.step_assets ? "当前步骤示意" : "请结合图示阅读当前步骤"}`;
        previous.dataset.ready = String(step > 0 && !context.readOnly);
        previous.disabled =
          previous.dataset.ready !== "true" ||
          (!context.preview && Boolean(context.isBusy?.()));
        next.textContent =
          step === data.steps.length - 1
            ? context.preview
              ? "最后一步"
              : "填写结果"
            : "下一步";
        next.dataset.ready = String(
          !context.readOnly &&
            (step < data.steps.length - 1 ||
              (!context.preview && Boolean(context.onAnswer))),
        );
        next.disabled =
          next.dataset.ready !== "true" ||
          (!context.preview && Boolean(context.isBusy?.()));
        if (focusInstruction) {
          instruction.tabIndex = -1;
          instruction.focus({ preventScroll: true });
        }
      }
      update();
      const details = compactGuide
        ? fullInstruction
        : el("details", "task-details");
      if (compactGuide) {
        details.classList.add("task-details");
        details.querySelector("summary").textContent =
          "完整操作说明 · 使用限制";
      } else details.append(el("summary", "", "为什么观察 · 使用限制"));
      details.append(el("p", "task-purpose", data.purpose));
      if (compactGuide && data.equipment.length)
        details.append(el("p", "", `准备：${data.equipment.join("、")}`));
      data.clinical_limits.forEach((item) => details.append(el("p", "", item)));
      const prompt = el(
        "p",
        "task-prompt",
        context.preview
          ? "这是素材预览，不会记录结果或改变问诊进度。"
          : data.response_prompt,
      );
      // The action and composer already supply the short prompt; retain its full
      // wording on demand instead of repeating it next to the illustration.
      if (!context.preview && (compactGuide || data.id === "body_location"))
        details.append(prompt);
      card.append(
        el("p", "task-stop", `停止 / 跳过：${data.stop_conditions.join("、")}`),
        details,
      );
      if (!prompt.parentNode) card.append(prompt);
      return card;
    },
  );

  register(
    "summary",
    (data) => {
      if (data.observations !== undefined)
        list(data.observations).forEach(validateObservations);
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
      if (data.risk_triggers !== undefined)
        list(data.risk_triggers).forEach(text);
      if (
        data.review_status !== undefined &&
        !["pending_review", "confirmed_by_user", "closed_unconfirmed"].includes(
          data.review_status,
        )
      )
        throw new Error("Invalid summary review status");
    },
    (data, context) => {
      const reviewStatus =
        data.review_status ||
        context.reviewStatus ||
        (context.readOnly ? "closed_unconfirmed" : "pending_review");
      const reviewCopy = {
        pending_review: {
          kicker: "核对记录 · 可以更正",
          intro: "请核对已记录的信息。“未知”表示尚未确认。",
          label: "待你核对",
          detail: "不准确可直接更正；确认后会结束本次问诊。",
          record: "待核对",
        },
        confirmed_by_user: {
          kicker: "会谈记录 · 已由你确认",
          intro: "这是你已确认的本次记录；“未知”表示当时尚未确认。",
          label: "你已确认",
          detail: "会谈已结束，可展开记录回看原话与来源。",
          record: "已确认",
        },
        closed_unconfirmed: {
          kicker: "会谈记录 · 未经核对",
          intro: "系统因分流建议结束了会谈；“未知”表示尚未确认。",
          label: "会谈已结束，尚未核对",
          detail: "请以原始记录为准，并按页面建议尽快求助。",
          record: "未核对",
        },
      }[reviewStatus];
      const card = el("article", "summary-card");
      card.append(
        el("p", "task-kicker", reviewCopy.kicker),
        el("h3", "", "本次问诊摘要"),
      );
      card.append(el("p", "summary-intro", reviewCopy.intro));
      const review = el("div", "summary-review-status");
      review.setAttribute("role", "status");
      review.append(
        el("strong", "", reviewCopy.label),
        el("span", "", reviewCopy.detail),
      );
      card.append(review);
      if (context.reviewFeedback) {
        const feedback = el("div", "summary-correction-feedback");
        feedback.append(el("span", "", context.reviewFeedback.message));
        if (context.onUndoCorrection && context.reviewFeedback.factId) {
          const undo = button(
            "撤销本次更正",
            () => context.onUndoCorrection(context.reviewFeedback),
            "summary-undo",
          );
          undo.disabled = Boolean(context.readOnly);
          feedback.append(undo);
        }
        card.append(feedback);
      }
      // The closing guidance leads the card: what to do next and which
      // changes need immediate help must not sit below the full fact list.
      // When a conclusion sheet is present it owns D1/D5, and this card
      // renders in record mode (design: 结论单 §7 de-duplication).
      if (!context.conclusionPresent) {
        const riskLevel = ["emergency", "urgent", "routine"].includes(
          data.risk_level,
        )
          ? data.risk_level
          : "routine";
        const route = el("section", `summary-route ${riskLevel}`);
        route.setAttribute(
          "aria-label",
          riskLevel === "urgent" ? "尽快就医评估建议" : "下一步建议",
        );
        if (riskLevel === "urgent") route.setAttribute("role", "alert");
        route.append(
          el(
            "p",
            "summary-route-label",
            riskLevel === "urgent" ? "尽快就医评估" : "下一步建议",
          ),
          el("p", "summary-route-action", data.care_route),
        );
        card.append(route);
        if (data.return_now_if?.length) {
          const safety = el("section", "summary-safety");
          safety.setAttribute("aria-label", "出现以下变化请立即求助");
          safety.append(
            el("p", "summary-safety-label", "出现以下变化，请立即求助"),
          );
          const safetyList = el("ul", "summary-safety-list");
          data.return_now_if.forEach((item) =>
            safetyList.append(el("li", "", item)),
          );
          safety.append(safetyList);
          card.append(safety);
        }
      }
      const groups = [
        { title: "当前不适", icon: "activity", items: [] },
        { title: "观察记录", icon: "person", items: [] },
        { title: "用药与背景", icon: "note", items: [] },
      ];
      const highlights = data.known.filter(
        (item) =>
          ["age_years", "temperature_c", "severity"].includes(item.key) &&
          !(data.observations || []).some((series) => series.key === item.key),
      );
      (data.observations || []).forEach((series) =>
        card.append(renderObservations(series, context)),
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
        if (item.structured_input?.schema === "location-answer/v1") {
          value.replaceChildren(
            renderLocationChips(item.structured_input.answers),
          );
        }
        const recordState = el("div", "summary-record-state");
        recordState.append(
          el("span", "", item.structured_input ? "已确认选项" : "系统整理"),
          el("span", "", reviewCopy.record),
        );
        value.append(recordState);
        if (item.key !== "temperature_c" || item.evidence || item.context) {
          const provenance = el("details", "summary-provenance");
          const reported = ["patient", "caregiver"].includes(item.source);
          provenance.append(
            el(
              "summary",
              "",
              item.structured_input
                ? "确认选项与来源"
                : item.evidence
                  ? reported
                    ? "原话与来源"
                    : "依据与来源"
                  : "来源",
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
          if (item.structured_input?.proposal_source?.evidence) {
            const draftSource = el("details");
            draftSource.append(
              el(
                "summary",
                "",
                item.structured_input.proposal_source.origin === "record"
                  ? "修改前的记录"
                  : item.structured_input.proposal_source.origin ===
                      "revision_proposal"
                    ? "提出更正时的原话"
                    : "整理时的原话",
              ),
              el(
                "blockquote",
                "",
                item.structured_input.proposal_source.evidence,
              ),
            );
            provenance.append(draftSource);
            if (item.structured_input.proposal_source.baseline?.evidence) {
              const baseline = el("details");
              baseline.append(
                el("summary", "", "修改前的记录"),
                el(
                  "blockquote",
                  "",
                  item.structured_input.proposal_source.baseline.evidence,
                ),
              );
              provenance.append(baseline);
            }
          }
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
        if (
          !context.readOnly &&
          item.key === "symptom_location" &&
          item.fact_id === context.locationRevisionAction?.fact_id
        ) {
          const revise = button(
            "修改定位",
            () => context.onReviseLocation?.(item.fact_id),
            "fact-edit location-revision-start",
          );
          revise.dataset.actionId = context.actionId;
          revise.dataset.factId = item.fact_id;
          row.append(revise);
        }
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
          if (
            !context.readOnly &&
            report.key === "symptom_location" &&
            report.fact_id === context.locationRevisionAction?.fact_id
          ) {
            const revise = button(
              "修改定位",
              () => context.onReviseLocation?.(report.fact_id),
              "fact-edit location-revision-start",
            );
            revise.dataset.actionId = context.actionId;
            revise.dataset.factId = report.fact_id;
            heading.append(revise);
          }
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
        (item) =>
          !highlights.includes(item) &&
          !(data.observations || []).some((series) => series.key === item.key),
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
      if (data.risk_triggers?.length) {
        const reasons = el("details", "summary-risk-triggers");
        reasons.append(
          el("summary", "", "查看本次需要尽快评估的原因"),
          el("p", "", data.risk_triggers.join("；")),
        );
        card.append(reasons);
      }
      return card;
    },
  );

  register(
    "conclusion",
    (data) => {
      if (!["routine", "urgent"].includes(data.tier))
        throw new Error("Invalid conclusion tier");
      const disposition = data.disposition || {};
      if (
        !["urgent_today", "routine_visit", "self_care"].includes(
          disposition.level,
        )
      )
        throw new Error("Invalid disposition level");
      text(disposition.text);
      if (
        disposition.window_hours !== undefined &&
        disposition.window_hours !== null &&
        !Number.isFinite(disposition.window_hours)
      )
        throw new Error("Invalid disposition window");
      list(data.directions).forEach((item) => {
        if (!["likely", "rule_out"].includes(item.group))
          throw new Error("Invalid direction group");
        if (
          !["more_likely", "possible", "cannot_exclude"].includes(
            item.certainty,
          )
        )
          throw new Error("Invalid direction certainty");
        if (item.patient_label !== null && item.patient_label !== undefined)
          text(item.patient_label);
        list(item.support_quotes).forEach(text);
        if (item.against_note) text(item.against_note);
      });
      list(data.gaps).forEach((gap) => {
        text(gap.label);
        text(gap.impact);
      });
      const care = data.self_care || {};
      list(care.do).forEach(text);
      list(care.avoid).forEach(text);
      list(care.measure).forEach(text);
      list(data.return_now_if).forEach(text);
      if (data.outlook) text(data.outlook);
      if (data.abstain_text) text(data.abstain_text);
    },
    (data) => {
      const card = el("article", "conclusion-card");
      card.append(el("p", "conclusion-kicker", "问诊结论"));
      const disposition = el(
        "section",
        `conclusion-disposition ${data.disposition.level}`,
      );
      if (data.tier === "urgent") disposition.setAttribute("role", "alert");
      disposition.append(
        el("p", "conclusion-disposition-label", "下一步"),
        el("p", "conclusion-disposition-text", data.disposition.text),
      );
      card.append(disposition);
      const you = data.subject_kind === "self" ? "你" : "您";
      if (data.directions_visible) {
        const directions = el("section", "conclusion-directions");
        directions.append(el("h4", "", "可能的方向（不是确诊）"));
        if (data.directions_abstained) {
          directions.append(el("p", "conclusion-abstain", data.abstain_text));
        } else {
          const templates = {
            more_likely: (label) => `更像是 ${label}`,
            possible: (label) => `可能是 ${label}`,
            cannot_exclude: (label) =>
              `目前还不能排除 ${label}，这也是建议就诊的原因`,
          };
          for (const group of ["likely", "rule_out"]) {
            const items = data.directions.filter(
              (item) => item.group === group && item.patient_label,
            );
            if (!items.length) continue;
            directions.append(
              el(
                "p",
                "conclusion-direction-group",
                group === "likely" ? "更可能的方向" : "需要排除的方向",
              ),
            );
            for (const item of items) {
              const box = el("div", `conclusion-direction ${group}`);
              box.append(
                el("strong", "", templates[item.certainty](item.patient_label)),
              );
              if (item.support_quotes?.length)
                box.append(
                  el(
                    "p",
                    "conclusion-direction-basis",
                    `依据：${you}提到「${item.support_quotes.join("」、「")}」。`,
                  ),
                );
              if (item.against_note)
                box.append(
                  el("p", "conclusion-direction-basis", item.against_note + "。"),
                );
              directions.append(box);
            }
          }
        }
        card.append(directions);
      }
      if (data.gaps?.length) {
        const gaps = el("section", "conclusion-gaps");
        gaps.append(el("h4", "", "还缺的信息"));
        const listEl = el("ul");
        data.gaps.forEach((gap) => {
          const li = el("li");
          li.append(el("strong", "", gap.label), `——${gap.impact}`);
          listEl.append(li);
        });
        gaps.append(listEl);
        card.append(gaps);
      }
      const care = data.self_care || {};
      if (care.do?.length || care.avoid?.length || care.measure?.length) {
        const careEl = el("section", "conclusion-care");
        careEl.append(el("h4", "", "现在能做的事"));
        const addList = (items, label) => {
          if (!items?.length) return;
          if (label) careEl.append(el("p", "conclusion-care-label", label));
          const ul = el("ul");
          items.forEach((item) => ul.append(el("li", "", item)));
          careEl.append(ul);
        };
        addList(care.do);
        addList(care.measure, "可以测一测");
        addList(care.avoid, "不要做的事");
        card.append(careEl);
      }
      if (data.return_now_if?.length) {
        const safety = el("section", "summary-safety");
        safety.setAttribute("aria-label", "出现以下变化请立即求助");
        safety.append(
          el("p", "summary-safety-label", "出现以下变化，请立即求助"),
        );
        const safetyList = el("ul", "summary-safety-list");
        data.return_now_if.forEach((item) =>
          safetyList.append(el("li", "", item)),
        );
        safety.append(safetyList);
        card.append(safety);
      }
      if (data.outlook || data.follow_up) {
        const outlook = el("section", "conclusion-outlook");
        outlook.append(el("h4", "", "接下来"));
        if (data.outlook) outlook.append(el("p", "", data.outlook + "。"));
        if (data.follow_up?.due_in_hours)
          outlook.append(
            el(
              "p",
              "",
              `如果到 ${data.follow_up.due_in_hours} 小时后还没有好转，或出现上面任何警示信号，请线下就医，不要等随访。`,
            ),
          );
        card.append(outlook);
      }
      card.append(
        el("p", "conclusion-handoff", "就诊时可把本次记录给医生看。"),
        el(
          "p",
          "conclusion-disclaimer",
          "以上是根据你提供的信息给出的分诊与健康建议，不替代医生面诊。",
        ),
      );
      return card;
    },
  );

  register(
    "follow_up",
    (data) => {
      if (!["routine_checkin", "urgent_checkin"].includes(data.kind))
        throw new Error("Invalid follow-up kind");
      if (!Number.isFinite(data.due_in_hours) || data.due_in_hours <= 0)
        throw new Error("Invalid follow-up due time");
      text(data.reason);
      if (data.status !== undefined) text(data.status);
      if (data.due_at !== undefined && data.due_at !== null)
        text(data.due_at);
    },
    (data) => {
      const urgent = data.kind === "urgent_checkin";
      const card = el("article", "followup-card");
      card.append(el("p", "task-kicker", "后续随访"));
      card.append(
        el(
          "h3",
          "",
          urgent ? "请确认是否已获得线下评估" : "例行回访 · 确认恢复情况",
        ),
      );
      const due = el("p", "followup-due");
      due.append(
        el("strong", "", `约 ${data.due_in_hours} 小时后`),
        document.createTextNode(
          "到期。到期后可以记录当时的情况：已恢复、好转、没变化、加重，或已经就医。",
        ),
      );
      card.append(due);
      if (data.reason)
        card.append(el("p", "followup-reason", data.reason));
      card.append(
        el(
          "p",
          "followup-note",
          "如果情况加重或出现上方警示信号，请不要等随访，立即就医并重新评估。",
        ),
      );
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
      if (data.followup !== undefined)
        validateCalculationFollowup(data.followup);
      if (data.subject_label !== undefined) text(data.subject_label);
      if (
        data.approximate !== undefined &&
        typeof data.approximate !== "boolean"
      )
        throw new Error("Invalid approximation");
      if (data.precision_policy !== undefined) text(data.precision_policy);
      if (data.references !== undefined) {
        if (list(data.references).length > 8)
          throw new Error("Too many references");
        data.references.forEach((ref) => {
          for (const key of ["title", "url", "section", "accessed"])
            text(ref[key]);
          const link = new URL(ref.url);
          if (link.protocol !== "https:" || link.username || link.password)
            throw new Error("Invalid formula reference");
        });
      }
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
      if (
        data.provenance.capability_id === "calculate.glucose" &&
        (typeof data.approximate !== "boolean" ||
          !data.precision_policy?.trim() ||
          !data.references?.length)
      )
        throw new Error("Missing glucose calculation basis");
      if (data.provenance.evidence !== undefined) {
        text(data.provenance.evidence.quote);
        text(data.provenance.evidence.source_turn_id);
        if (
          ![
            "quantity_and_source_unit_only",
            "analyte_quantity_and_source_unit_only",
          ].includes(data.provenance.evidence.scope)
        )
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
        el(
          "p",
          data.subject_label ? "calculation-title" : "task-kicker",
          data.subject_label || "显式计算",
        ),
        el(
          "span",
          "calculation-status",
          `已计算 · ${inputStates[data.input_status]}`,
        ),
      );
      card.append(
        top,
        el(
          "h3",
          "",
          `${data.approximate ? "≈ " : ""}${data.value} ${data.unit}`,
        ),
      );
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
            ? "输入已核对；实际测量质量未评价。"
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
      if (data.followup)
        details.append(renderCalculationFollowup(data.followup, context));
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
            data.provenance.evidence.scope ===
              "analyte_quantity_and_source_unit_only"
              ? "原文血糖项目、数值与原单位已核对；目标单位仍为模型提议，实际测量尚未确认。"
              : "原文数值与原单位已核对；目标单位仍为模型提议，实际测量尚未确认。",
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
      if (data.precision_policy)
        details.append(el("p", "", data.precision_policy));
      if (data.references?.length) {
        const refs = el("div", "calculation-references");
        refs.append(el("strong", "", "公式来源"));
        for (const ref of data.references) {
          const link = el("a", "", ref.title);
          link.href = ref.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          refs.append(
            link,
            el("p", "", `${ref.section} · 核对日期 ${ref.accessed}`),
          );
        }
        details.append(refs);
      }
      card.append(details);
      if (context.onContinueCalculation)
        card.append(
          button(
            data.followup?.status === "pending" ? "继续处理补充" : "继续问诊",
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
