export const $ = (selector) => document.querySelector(selector);

export function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined && text !== null) value.textContent = String(text);
  return value;
}

export function action(label, className, callback) {
  const value = node("button", className, label);
  value.type = "button";
  value.addEventListener("click", callback);
  return value;
}

// S3 visualization simplification: a titled toggle + hidden body.
// The toggle text can be refreshed later (counts / filled status).
export function collapsePair(label) {
  const body = node("div", "records-collapse-body");
  body.hidden = true;
  const toggle = action(label, "records-collapse-toggle", () => {
    body.hidden = !body.hidden;
    toggle.setAttribute("aria-expanded", body.hidden ? "false" : "true");
  });
  toggle.setAttribute("aria-expanded", "false");
  return { toggle, body };
}

export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function localDateInput(date = new Date()) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

export function isoTime(value, required = false) {
  if (!value && !required) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) throw new Error("请填写有效的日期和时间");
  return parsed.toISOString();
}

export function dateParts(value) {
  if (!value) return { day: "--", rest: "时间待补充" };
  const parsed = new Date(value);
  return {
    day: new Intl.DateTimeFormat("zh-CN", { day: "2-digit" }).format(parsed),
    rest: new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "short",
    }).format(parsed),
  };
}

export function formatTime(value) {
  if (!value) return "时间待补充";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(reader.result));
    reader.addEventListener("error", () =>
      reject(new Error("无法读取这张图片")),
    );
    reader.readAsDataURL(file);
  });
}

export const statuses = {
  awaiting_recognition: "等待识别",
  recognizing: "正在识别",
  recognition_failed: "识别未完成",
  needs_confirmation: "有内容待核对",
  ready: "已归档",
};

export const verification = {
  model_extracted: "模型识别",
  needs_confirmation: "待你核对",
  patient_confirmed: "你已确认",
  patient_corrected: "你已修正",
};

/*
 * P3a 报告解读（设计 §5.1，产出合同 report-interpretation/v1）。
 * 分级只做呈现：绿/黄/红/无法判断；无法识别的分级一律按「无法判断」
 * （灰点）呈现，不升级、不猜测。固定声明必须原样展示。
 */
export const INTERPRETATION_DISCLAIMER =
  "由 AI 生成，未经医生审核；分级依据为工程版参考区间";

export const INTERPRETATION_GRADES = {
  green: { label: "绿", dot: "interp-dot--green" },
  yellow: { label: "黄", dot: "interp-dot--yellow" },
  red: { label: "红", dot: "interp-dot--red" },
  unknown: { label: "无法判断", dot: "interp-dot--unknown" },
};

export function normalizeInterpretationGrade(raw) {
  const text = String(raw ?? "").trim();
  const map = {
    绿: "green",
    green: "green",
    黄: "yellow",
    yellow: "yellow",
    红: "red",
    red: "red",
    无法判断: "unknown",
    unknown: "unknown",
  };
  return map[text] || "unknown";
}

/*
 * P3c 识别轮询（设计 §5.3）：每 2 秒查一次条目状态，最多 45 次
 * （90 秒上限，覆盖后端 45 秒识别超时 + 余量）；到上限仍未完成时
 * 停止轮询并如实提示，不无限轮询、不伪造完成。
 */
export const RECOGNITION_POLL = { intervalMs: 2000, maxAttempts: 45 };

/*
 * 纯渲染：由一份解读数据建出「解读」节 DOM（不发请求、不碰状态），
 * 供条目详情与 UI 用例共用。字段名做宽容映射（后端字段名以
 * P3-A 接线说明为准；未知字段不丢弃整项，只按「无法判断」呈现）。
 */
export function createInterpretationSection(interpretation) {
  const section = node("section", "record-interpretation");
  section.append(node("h3", "", "解读"));
  const data = interpretation || {};
  const items = data.items || data.results || data.entries || [];
  // 后端 report-interpretation/v1 的 summary 是对象（text 在内），
  // 字符串 summary 亦接受；对象无 text 时不渲染 “[object Object]”。
  const summaryText =
    typeof data.summary === "string"
      ? data.summary
      : data.summary?.text || data.overall || "";
  if (summaryText) section.append(node("p", "", summaryText));
  if (items.length) {
    const list = node("div", "record-interpretation-list");
    for (const item of items) {
      const gradeKey = normalizeInterpretationGrade(item.grade ?? item.level);
      const grade = INTERPRETATION_GRADES[gradeKey];
      const row = node("div", "record-interpretation-item");
      const head = node("div", "record-interpretation-head");
      const dot = node("span", `interp-dot ${grade.dot}`);
      dot.setAttribute("aria-label", `分级${grade.label}`);
      head.append(
        dot,
        node(
          "strong",
          "",
          item.display_name ||
            item.common_name ||
            item.name ||
            item.observation_ref ||
            "未命名项目",
        ),
        node(
          "span",
          "",
          item.result_text ||
            item.value_text ||
            [item.value, item.unit].filter(Boolean).join(" ") ||
            "",
        ),
        node("span", "interp-grade", grade.label),
      );
      row.append(head);
      const explanation =
        item.explanation || item.plain_language || item.description || "";
      if (explanation) row.append(node("p", "", explanation));
      const action = item.action || item.action_advice || item.recommendation;
      if (action) row.append(node("span", "subtle-pill", `建议：${action}`));
      list.append(row);
    }
    section.append(list);
  }
  section.append(
    node(
      "p",
      "record-interpretation-disclaimer",
      data.disclaimer || INTERPRETATION_DISCLAIMER,
    ),
  );
  return section;
}

/*
 * P2 前端接口（均已落地，见 tests/test_patient_records_api.py）：
 *   GET    /api/patients                       → { patients: [...] }（含 archived）
 *   POST   /api/patients                       { display_name, ...基线字段 }
 *   GET    /api/patients/{id}                  → { patient }（基线字段扁平在内）
 *   PATCH  /api/patients/{id}                  → 基线字段 / display_name（部分更新）
 *   POST   /api/patients/{id}/archive          → 空 body，{ patient }（默认档案拒归档）
 *   POST   /api/patient/measurements/{slug}    → { value_text, unit, measured_at }
 *          （血压为 { systolic, diastolic, unit, measured_at }；slug 用连字符）
 *   GET    /api/patient/trends/{slug}          → 与血糖趋势相同的 shape
 *   GET    /api/patients/{id}/timeline         → 与默认时间线相同的 shape
 *   POST   /api/sessions/{id}/bind-patient     → { patient_id }，{ patient, state }
 *   POST   /api/patients/{id}/measurements/{slug} → 与默认路由同 shape，写该成员名下
 *   GET    /api/patients/{id}/trends/{slug}    → 与默认路由同 shape，读该成员
 * 注意：默认路由仍恒绑本人档案；查看成员时前端走成员路由（见
 * measurementApiBase），不再禁用、不再隐藏趋势区。
 * 任何接口返回 404 时前端只提示、不伪造成功。
 *
 * P3 前端接口（设计 §5.1/§5.3，均已落地）：
 *   GET    /api/patients/entries/{id}/interpretation
 *          → { interpretation: { summary, items: [...] } }，无解读时 404
 *   POST   /api/patients/entries/{id}/interpretations
 *          → { interpretation }；接口未就绪时只提示、不伪造解读
 *   时间线条目徽标以后端标志为准：has_interpretation === true
 *         （亦接受 interpreted === true 或 interpretation_status 为
 *          ready/completed）；无标志时不加徽标、不猜测。
 *   识别轮询：条目 status 为 recognizing 时 renderDetail 自动轮询
 *          GET /api/entries/{id}（RECOGNITION_POLL），识别完成或失败
 *          后自动刷新；POST recognitions 返回 202/recognizing 时同理。
 */
export const BASELINE_FIELDS = [
  "birth_year",
  "sex",
  "height_cm",
  "weight_kg",
  "chronic_conditions",
  "allergies",
  "medications",
  "surgical_history",
  "birth_year_month",
];

export const MEASUREMENT_KINDS = [
  {
    kind: "blood_glucose",
    slug: "blood-glucose",
    label: "血糖",
    units: ["mmol/L", "mg/dL"],
    defaultUnit: "mmol/L",
    legacy: true,
  },
  {
    kind: "blood_pressure",
    slug: "blood-pressure",
    label: "血压",
    units: ["mmHg"],
    defaultUnit: "mmHg",
    dual: true,
  },
  {
    kind: "temperature",
    slug: "temperature",
    label: "体温",
    units: ["°C", "°F"],
    defaultUnit: "°C",
  },
  {
    kind: "weight",
    slug: "weight",
    label: "体重",
    units: ["kg", "斤"],
    defaultUnit: "kg",
  },
  {
    kind: "blood_oxygen",
    slug: "blood-oxygen",
    label: "血氧",
    units: ["%"],
    defaultUnit: "%",
  },
];

export const ALLERGY_KINDS = [
  { value: "drug", label: "药物" },
  { value: "food", label: "食物" },
  { value: "other", label: "其他" },
];

/*
 * 问诊页档案引用横幅（P2c，设计 §4.3）。
 * 只负责 DOM 与点击回调占位：协调者在 app.js 挂载时传入 onDismiss
 * 接线记事件；本模块不写事件、不碰问诊状态。
 * 挂载示例：document.querySelector(问诊顶部容器)
 *   .append(createBaselineBanner({ onDismiss: () => recordBaselineBannerDismissed() }));
 */
export function createBaselineBanner({ onDismiss } = {}) {
  const banner = node("div", "baseline-banner");
  banner.id = "chengxin-baseline-banner";
  banner.setAttribute("role", "status");
  banner.append(
    node("span", "", "本次问诊参考了你的健康档案（慢病/过敏/用药）"),
    action("关闭", "text-button baseline-banner-close", () => {
      banner.hidden = true;
      onDismiss?.();
    }),
  );
  const close = banner.querySelector(".baseline-banner-close");
  close.setAttribute("aria-label", "关闭档案引用提示");
  return banner;
}

export function splitList(text) {
  return String(text || "")
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}
