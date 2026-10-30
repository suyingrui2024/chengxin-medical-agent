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

// Re-exported so the renderer family modules share one import surface.
export {
  renderQuestion,
  validateQuestion,
  renderBodyMap,
  visualIcon,
  renderLocationAnswer,
  validateLocationAnswerSpec,
  renderLocationChips,
};
export const UI_VERSION = "medical-ui/v1";
export let illustrationSequence = 0;
// Compact captions only apply to this exact, repository-owned instruction set.
// Other capability versions retain their complete supplied instructions.
export const temperatureGuide = [
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

export const sourceNames = {
  patient: "患者自述",
  caregiver: "照护者自述",
  device: "设备直连",
  camera: "相机观察",
  system_inference: "系统推断",
};
export const qualityNames = {
  not_assessed: "质量未评价",
  adequate: "质量可用",
  inadequate: "质量不足",
  not_done: "未执行",
};
export const inputStates = {
  unconfirmed: "输入待核对",
  confirmed: "输入已核对",
};

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null)
    node.textContent =
      typeof text === "object" ? JSON.stringify(text) : String(text);
  return node;
}
export function button(label, handler, className = "") {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
export function text(value) {
  if (typeof value !== "string" || value.length > 16000)
    throw new Error("Invalid text");
  return value;
}
export function list(value) {
  if (!Array.isArray(value) || value.length > 100)
    throw new Error("Invalid list");
  return value;
}
export function validateCalculationFollowup(data) {
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
export function renderCalculationFollowup(data, context) {
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
export function validateObservations(data) {
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
export function renderObservations(data, context) {
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
export function validateProvenance(item) {
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
export function sourceTurnContext(context, evidence) {
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
export function asset(value) {
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

// Renderer type tags shared by the forms and closing families.
export const SCALE_FORM = "scale_form/v1";
export const SCALE_RENDERER = "scale/v1";
export const FORMULA_FORM = "formula_form/v1";
export const FORMULA_RENDERER = "formula/v1";
