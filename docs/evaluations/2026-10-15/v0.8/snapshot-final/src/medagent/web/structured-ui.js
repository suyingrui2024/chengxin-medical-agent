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
      list(data.items).forEach(text);
    },
    (data) => {
      const node = el("article", "structured-notice");
      node.setAttribute("role", "alert");
      node.append(
        el("strong", "", data.title),
        el("p", "", data.items.join("、")),
      );
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
      });
      list(data.unknown).forEach(text);
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
        else {
          const provenance = el("details", "summary-provenance");
          provenance.append(
            el("summary", "", "来源"),
            el("div", "fact-source", source),
          );
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
        data.unknown.forEach((item) => chips.append(el("span", "", item)));
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
      if (
        !Object.hasOwn(inputStates, data.input_status) ||
        !Object.hasOwn(qualityNames, data.quality)
      )
        throw new Error("Invalid quality");
    },
    (data) => {
      const card = el("article", "calculation-card");
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
      card.append(
        inputs,
        el("p", "calculation-formula", data.formula),
        el("p", "", data.method),
      );
      card.append(
        el(
          "p",
          "calculation-caution",
          `${inputStates[data.input_status]} · ${qualityNames[data.quality]}。结果未写为临床事实。`,
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
            : "来源：模型提议输入",
        ),
        el("p", "", `能力组合：${data.provenance.registry_sha256}`),
      );
      data.limitations.forEach((item) => details.append(el("p", "", item)));
      card.append(details);
      return card;
    },
  );

  return { register, render, renderEnvelope, blocks };
}
