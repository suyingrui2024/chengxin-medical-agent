const $ = (selector) => document.querySelector(selector);

function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined && text !== null) value.textContent = String(text);
  return value;
}

function action(label, className, callback) {
  const value = node("button", className, label);
  value.type = "button";
  value.addEventListener("click", callback);
  return value;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function localDateInput(date = new Date()) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function isoTime(value, required = false) {
  if (!value && !required) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) throw new Error("请填写有效的日期和时间");
  return parsed.toISOString();
}

function dateParts(value) {
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

function formatTime(value) {
  if (!value) return "时间待补充";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(reader.result));
    reader.addEventListener("error", () =>
      reject(new Error("无法读取这张图片")),
    );
    reader.readAsDataURL(file);
  });
}

const statuses = {
  awaiting_recognition: "等待识别",
  recognizing: "正在识别",
  recognition_failed: "识别未完成",
  needs_confirmation: "有内容待核对",
  ready: "已归档",
};

const verification = {
  model_extracted: "模型识别",
  needs_confirmation: "待你核对",
  patient_confirmed: "你已确认",
  patient_corrected: "你已修正",
};

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
 * 注意：测量写入与趋势接口恒绑默认（本人）档案——查看非默认成员时
 * 前端禁用“记录测量”并隐藏趋势区（M2），不把成员数据记到本人名下。
 * 任何接口返回 404 时前端只提示、不伪造成功。
 */
const BASELINE_FIELDS = [
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

const MEASUREMENT_KINDS = [
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

const ALLERGY_KINDS = [
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

function splitList(text) {
  return String(text || "")
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function createPatientRecordsUI(api, toast, options = {}) {
  const state = {
    initialized: false,
    visionAvailable: false,
    timeline: null,
    glucoseTrend: null,
    previewUrl: null,
    busy: false,
    filter: "all",
    defaultPatient: null,
    members: [],
    membersReady: false,
    activePatientId: null,
    baseline: null,
    baselineReady: false,
    measurementTrends: {},
  };

  function setFormBusy(form, busy) {
    for (const control of form.elements) control.disabled = busy;
  }

  async function ensurePatient() {
    const catalog = await api("/api/patient");
    state.initialized = true;
    state.visionAvailable = catalog.vision_available;
    state.defaultPatient = catalog.patient || null;
    if (!state.activePatientId && state.defaultPatient)
      state.activePatientId = state.defaultPatient.patient_id;
  }

  function renderSummary(entries) {
    const target = $("#records-summary");
    target.replaceChildren();
    if (!entries.length) {
      target.hidden = true;
      return;
    }
    const pending = entries.reduce((sum, item) => sum + item.review_count, 0);
    const measurements = entries.filter(
      (item) => item.entry_type === "manual_measurement",
    ).length;
    for (const [count, label, accent] of [
      [pending, "待核对", pending > 0],
      [entries.length, "全部记录", false],
      [measurements, "自测数据", false],
    ]) {
      const card = node("article", accent ? "attention" : "");
      card.append(node("strong", "", count), node("span", "", label));
      target.append(card);
    }
    target.hidden = false;
  }

  function renderTimeline() {
    const entries = state.timeline?.entries || [];
    $("#records-count").textContent = String(entries.length);
    renderSummary(entries);
    const target = $("#records-timeline");
    target.replaceChildren();
    if (!entries.length) {
      const empty = node("div", "empty-state large");
      empty.append(
        icon("cards"),
        node("h2", "", "还没有健康记录"),
        node("p", "", "上传一张医疗图片，或记录一次血糖。不同日期会分别保存。"),
      );
      target.append(empty);
      return;
    }
    const visible = entries.filter((item) => {
      if (state.filter === "pending") return item.review_count > 0;
      if (state.filter === "manual")
        return item.entry_type === "manual_measurement";
      if (state.filter === "images")
        return item.entry_type === "uploaded_document";
      return true;
    });
    if (!visible.length) {
      const empty = node("div", "empty-state compact");
      empty.append(node("p", "", "这个分类下还没有记录。"));
      target.append(empty);
      return;
    }
    for (const item of visible) {
      const date = dateParts(
        item.measured_at || item.reported_at || item.uploaded_at,
      );
      const card = action("", "record-card", () =>
        openDetail(item.entry_id).catch((error) => toast(error.message)),
      );
      const dateNode = node("span", "record-date");
      dateNode.append(
        node("strong", "", date.day),
        document.createTextNode(date.rest),
      );
      const main = node("span", "record-main");
      main.append(node("strong", "", item.title));
      if (item.highlights?.length) {
        const highlights = node("span", "record-highlights");
        for (const observation of item.highlights) {
          highlights.append(
            node(
              "span",
              observation.verification_status === "needs_confirmation"
                ? "pending"
                : "",
              `${observation.display_name} ${observation.value_text || "待核对"}${observation.unit ? ` ${observation.unit}` : ""}`,
            ),
          );
        }
        main.append(highlights);
      } else {
        main.append(
          node(
            "small",
            "",
            item.entry_type === "manual_measurement"
              ? "患者手动录入"
              : "原图已归档",
          ),
        );
      }
      const status = node("span", "record-state");
      status.append(
        node(
          "span",
          `subtle-pill ${item.review_count ? "record-pending" : ""}`,
          item.pending_count
            ? `${item.pending_count} 项内容模糊`
            : item.review_count
              ? `${item.review_count} 项待确认`
              : statuses[item.status] || item.status,
        ),
        icon("arrow"),
      );
      card.append(dateNode, main, status);
      target.append(card);
    }
  }

  function renderGlucoseTrend() {
    const target = $("#records-trend");
    const trend = state.glucoseTrend;
    target.replaceChildren();
    if (!trend?.summary) {
      target.hidden = true;
      return;
    }
    const copy = node("div");
    copy.append(
      node("h2", "", `近期血糖 · ${trend.count} 次已确认记录`),
      node(
        "p",
        "",
        `平均 ${trend.summary.average}，范围 ${trend.summary.minimum}–${trend.summary.maximum} mmol/L。仅展示描述性变化，不判断高低。`,
      ),
    );
    const visual = node("div", "records-trend-visual");
    if (trend.points.length > 1) {
      const values = trend.points.map((point) =>
        Number(point.normalized_value),
      );
      const minimum = Math.min(...values);
      const maximum = Math.max(...values);
      const spread = maximum - minimum || 1;
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 180 48");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", `近期 ${values.length} 次血糖变化`);
      const points = values
        .map((value, index) => {
          const x = 8 + (index * 164) / (values.length - 1);
          const y = 40 - ((value - minimum) / spread) * 32;
          return `${x},${y}`;
        })
        .join(" ");
      const line = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "polyline",
      );
      line.setAttribute("points", points);
      svg.append(line);
      visual.append(svg);
    }
    const latest = node("div", "records-trend-value");
    latest.append(
      node("strong", "", `${trend.summary.latest.normalized_value} mmol/L`),
      node(
        "small",
        "",
        `最近一次 · ${formatTime(trend.summary.latest.measured_at)}`,
      ),
    );
    visual.append(latest);
    target.append(copy, visual);
    target.hidden = false;
  }

  function activeMember() {
    return (
      state.members.find((item) => item.patient_id === state.activePatientId) ||
      state.defaultPatient
    );
  }

  // M2: measurement writes and trend reads are hard-bound to the
  // default (self) profile server-side, so they are only meaningful
  // while the default member is the one being viewed.
  function viewingDefaultMember() {
    const member = activeMember();
    return Boolean(
      member &&
      state.defaultPatient &&
      member.patient_id === state.defaultPatient.patient_id,
    );
  }

  function syncMeasurementAvailability() {
    const button = $("#add-measurement");
    const isDefault = viewingDefaultMember();
    if (button) {
      button.disabled = !isDefault;
      button.title = isDefault ? "" : "测量记录目前只支持本人档案";
    }
    if (!isDefault) {
      // Hide the self profile's trends rather than showing them under
      // a member's name; the member-list notice (renderMembers) says
      // so explicitly, so the absence is explained, not silent.
      const glucose = $("#records-trend");
      if (glucose) glucose.hidden = true;
      const trends = $("#records-measurement-trends");
      if (trends) trends.hidden = true;
    } else {
      // Switching back re-renders from the cached trends; refresh()
      // already rendered them, so this is only a no-op safety net
      // for the member-switch path, which does not reload trends.
      renderGlucoseTrend();
      renderMeasurementTrends();
    }
  }

  function isArchived(member) {
    return Boolean(
      member &&
      (member.archived === true ||
        member.is_archived === true ||
        member.status === "archived"),
    );
  }

  function normalizeMembers(data) {
    const list = Array.isArray(data)
      ? data
      : data?.patients || (data?.patient ? [data.patient] : []);
    return list
      .filter((item) => item && item.patient_id)
      .map((item) => ({
        ...item,
        display_name:
          item.display_name || item.name || `成员 ${item.patient_id}`,
        is_default:
          item.is_default === true ||
          (state.defaultPatient &&
            item.patient_id === state.defaultPatient.patient_id),
      }));
  }

  function fieldRow(labelText, control) {
    const label = node("label", "field");
    label.append(node("span", "", labelText), control);
    return label;
  }

  function textInput(id, { type = "text", maxLength = 200 } = {}) {
    const input = node("input");
    input.id = id;
    input.type = type;
    input.maxLength = maxLength;
    return input;
  }

  function allergyRow(kind = "drug", nameValue = "") {
    const row = node("div", "baseline-row");
    const kindSelect = node("select");
    kindSelect.setAttribute("aria-label", "过敏种类");
    for (const item of ALLERGY_KINDS) {
      const option = node("option", "", item.label);
      option.value = item.value;
      if (item.value === kind) option.selected = true;
      kindSelect.append(option);
    }
    const nameInput = textInput("", { maxLength: 120 });
    nameInput.setAttribute("aria-label", "过敏原名称");
    nameInput.placeholder = "过敏原名称";
    nameInput.value = nameValue;
    row.append(
      kindSelect,
      nameInput,
      action("移除", "text-button", () => row.remove()),
    );
    row.dataset.role = "allergy-row";
    return row;
  }

  function medicationRow(nameValue = "", purposeValue = "") {
    const row = node("div", "baseline-row");
    const nameInput = textInput("", { maxLength: 120 });
    nameInput.setAttribute("aria-label", "药品名称");
    nameInput.placeholder = "药品名称";
    nameInput.value = nameValue;
    const purposeInput = textInput("", { maxLength: 120 });
    purposeInput.setAttribute("aria-label", "用途");
    purposeInput.placeholder = "用途（可选）";
    purposeInput.value = purposeValue;
    row.append(
      nameInput,
      purposeInput,
      action("移除", "text-button", () => row.remove()),
    );
    row.dataset.role = "medication-row";
    return row;
  }

  function ensureRecordsSections() {
    const view = $("#view-records");
    if (!view || $("#records-baseline")) return;
    const summary = $("#records-summary");

    const baseline = node("section", "records-baseline");
    baseline.id = "records-baseline";
    baseline.append(
      node("h2", "", "基线信息"),
      node(
        "p",
        "records-baseline-hint",
        "由你自己填写、可随时修改；问诊不会自动把对话内容写回这里。",
      ),
      node("div", "", ""),
    );
    baseline.lastChild.id = "baseline-summary";
    baseline.lastChild.className = "baseline-summary";
    const form = node("form", "baseline-form");
    form.id = "baseline-form";
    const birthYear = textInput("baseline-birth-year", {
      type: "number",
      maxLength: 4,
    });
    birthYear.inputMode = "numeric";
    const birthYearMonth = textInput("baseline-birth-year-month", {
      type: "month",
    });
    const sex = node("select");
    sex.id = "baseline-sex";
    for (const [value, label] of [
      ["", "未填写"],
      ["male", "男"],
      ["female", "女"],
      ["other", "其他"],
    ]) {
      const option = node("option", "", label);
      option.value = value;
      sex.append(option);
    }
    const height = textInput("baseline-height", { type: "number" });
    height.inputMode = "decimal";
    const weight = textInput("baseline-weight", { type: "number" });
    weight.inputMode = "decimal";
    const chronic = node("textarea");
    chronic.id = "baseline-chronic";
    chronic.maxLength = 1000;
    chronic.placeholder = "多项可用顿号或换行分隔";
    const allergies = node("div", "baseline-list");
    allergies.id = "baseline-allergies";
    const medications = node("div", "baseline-list");
    medications.id = "baseline-medications";
    const surgical = node("textarea");
    surgical.id = "baseline-surgical";
    surgical.maxLength = 1000;
    form.append(
      fieldRow("出生年", birthYear),
      fieldRow("出生年月（儿童成员请填写，用于月龄计算）", birthYearMonth),
      fieldRow("性别", sex),
      fieldRow("身高（cm）", height),
      fieldRow("体重（kg）", weight),
      fieldRow("慢病", chronic),
      node("span", "field-label", "过敏史"),
      allergies,
      action("添加过敏史", "secondary-button", () => {
        allergies.append(allergyRow());
      }),
      node("span", "field-label", "长期用药"),
      medications,
      action("添加长期用药", "secondary-button", () => {
        medications.append(medicationRow());
      }),
      fieldRow("手术 / 住院史摘要", surgical),
    );
    const save = node("button", "primary-button", "保存基线信息");
    save.type = "submit";
    form.append(save);
    form.addEventListener("submit", saveBaseline);
    baseline.append(form, node("p", "form-feedback", ""));
    baseline.lastChild.id = "baseline-feedback";
    baseline.lastChild.setAttribute("role", "status");
    view.insertBefore(baseline, summary);

    const members = node("section", "records-members");
    members.id = "records-members";
    members.append(
      node("h2", "", "家庭成员"),
      node(
        "p",
        "records-members-hint",
        "本人与成员数据分区展示；归档成员置灰，不可选为问诊对象。",
      ),
      node("div", "member-list"),
    );
    members.lastChild.id = "member-list";
    const addForm = node("form", "member-add-form");
    addForm.id = "member-add-form";
    const addName = textInput("member-add-name", { maxLength: 80 });
    addName.required = true;
    addName.placeholder = "成员姓名";
    addForm.append(
      fieldRow("新增成员", addName),
      (() => {
        const button = node("button", "secondary-button", "添加成员");
        button.type = "submit";
        return button;
      })(),
    );
    addForm.addEventListener("submit", addMember);
    members.append(addForm, node("p", "form-feedback", ""));
    members.lastChild.id = "member-feedback";
    members.lastChild.setAttribute("role", "status");
    view.insertBefore(members, summary);

    const trends = node("section", "records-measurement-trends");
    trends.id = "records-measurement-trends";
    trends.hidden = true;
    trends.append(
      node("h2", "", "自测趋势"),
      node("p", "", "按测量种类分组；仅展示数值统计，不做高低判断。"),
      node("div", "measurement-trend-list"),
    );
    trends.lastChild.id = "measurement-trend-list";
    $("#records-trend").after(trends);

    const actions = view.querySelector(".records-actions");
    if (actions && !$("#add-measurement")) {
      const button = node("button", "secondary-button", "记录测量");
      button.id = "add-measurement";
      button.type = "button";
      button.addEventListener("click", openMeasurementDialog);
      actions.append(button);
    }
    if (!$("#measurement-record-dialog"))
      document.body.append(buildMeasurementDialog());
    syncMeasurementAvailability();
  }

  function fillBaselineForm(baselineData) {
    const form = $("#baseline-form");
    if (!form) return;
    const value = baselineData || {};
    $("#baseline-birth-year").value = value.birth_year ?? "";
    $("#baseline-birth-year-month").value = value.birth_year_month || "";
    $("#baseline-sex").value = value.sex || "";
    $("#baseline-height").value = value.height_cm ?? "";
    $("#baseline-weight").value = value.weight_kg ?? "";
    $("#baseline-chronic").value = Array.isArray(value.chronic_conditions)
      ? value.chronic_conditions.join("、")
      : value.chronic_conditions || "";
    const allergies = $("#baseline-allergies");
    allergies.replaceChildren();
    for (const item of value.allergies || [])
      allergies.append(
        allergyRow(item.kind || "drug", item.name || item.value_text || ""),
      );
    const medications = $("#baseline-medications");
    medications.replaceChildren();
    for (const item of value.medications || [])
      medications.append(medicationRow(item.name || "", item.purpose || ""));
  }

  function collectBaseline() {
    const numberOrNull = (raw) => {
      if (raw === "" || raw === null || raw === undefined) return null;
      const parsed = Number(raw);
      return Number.isNaN(parsed) ? null : parsed;
    };
    const allergies = [];
    for (const row of document.querySelectorAll(
      "#baseline-allergies [data-role='allergy-row']",
    )) {
      const name = row.querySelector("input")?.value.trim();
      const kind = row.querySelector("select")?.value || "other";
      if (name) allergies.push({ kind, name });
    }
    const medications = [];
    for (const row of document.querySelectorAll(
      "#baseline-medications [data-role='medication-row']",
    )) {
      const inputs = row.querySelectorAll("input");
      const name = inputs[0]?.value.trim();
      if (name)
        medications.push({ name, purpose: inputs[1]?.value.trim() || "" });
    }
    return {
      birth_year: numberOrNull($("#baseline-birth-year").value),
      birth_year_month: $("#baseline-birth-year-month").value || null,
      sex: $("#baseline-sex").value || null,
      height_cm: numberOrNull($("#baseline-height").value),
      weight_kg: numberOrNull($("#baseline-weight").value),
      chronic_conditions: splitList($("#baseline-chronic").value),
      allergies,
      medications,
      surgical_history: $("#baseline-surgical").value.trim() || null,
    };
  }

  function renderBaselineSummary() {
    const target = $("#baseline-summary");
    if (!target) return;
    const value = state.baseline;
    if (!value) {
      target.textContent = "尚未填写基线信息。";
      return;
    }
    const parts = [];
    if (value.chronic_conditions?.length)
      parts.push(`慢病：${value.chronic_conditions.join("、")}`);
    if (value.allergies?.length)
      parts.push(
        `过敏：${value.allergies
          .map(
            (item) =>
              `${ALLERGY_KINDS.find((kind) => kind.value === item.kind)?.label || "其他"}·${item.name || item.value_text || ""}`,
          )
          .join("、")}`,
      );
    if (value.medications?.length)
      parts.push(
        `长期用药：${value.medications
          .map((item) =>
            item.purpose ? `${item.name}（${item.purpose}）` : item.name,
          )
          .join("、")}`,
      );
    target.textContent = parts.length
      ? parts.join("；")
      : "基线信息已保存，但慢病 / 过敏 / 用药均为空。";
  }

  async function loadMembers() {
    try {
      const data = await api("/api/patients");
      const list = normalizeMembers(data);
      if (!list.length) throw new Error("成员列表为空");
      state.members = list;
      state.membersReady = true;
    } catch {
      state.membersReady = false;
      state.members = state.defaultPatient
        ? [{ ...state.defaultPatient, is_default: true }]
        : [];
    }
    if (!state.activePatientId && state.defaultPatient)
      state.activePatientId = state.defaultPatient.patient_id;
    renderMembers();
  }

  function renderMembers() {
    const target = $("#member-list");
    if (!target) return;
    target.replaceChildren();
    if (!state.members.length) {
      target.append(node("p", "", "暂无成员信息。"));
      return;
    }
    for (const member of state.members) {
      const archived = isArchived(member);
      const card = node("article", `member-card${archived ? " archived" : ""}`);
      const head = node("div", "member-head");
      head.append(
        node("strong", "", member.display_name),
        node(
          "span",
          "subtle-pill",
          archived ? "已归档" : member.is_default ? "本人" : "成员",
        ),
      );
      if (state.activePatientId === member.patient_id)
        head.append(node("span", "subtle-pill", "当前查看"));
      card.append(head);
      const controls = node("div", "member-actions");
      if (!archived && state.activePatientId !== member.patient_id) {
        controls.append(
          action("切换查看", "secondary-button", () => {
            state.activePatientId = member.patient_id;
            renderMembers();
            loadBaseline().catch(() => {});
            refreshTimelineForActiveMember().catch((error) =>
              toast(error.message),
            );
          }),
        );
      }
      if (!archived) {
        // M4: bind this member as the current consultation's subject.
        // Session id comes from the same consultation context the
        // attach panel already uses (app.js getConsultationContext);
        // no second session concept is invented here. Without an
        // in-progress consultation the button is disabled, not hidden,
        // so the reason is visible.
        const consultation = options.getConsultationContext?.() || {};
        const bind = action(
          "设为本次问诊对象",
          "secondary-button",
          async (event) => {
            const trigger = event.currentTarget;
            trigger.disabled = true;
            try {
              const result = await api(
                `/api/sessions/${consultation.sessionId}/bind-patient`,
                {
                  method: "POST",
                  body: { patient_id: member.patient_id },
                },
              );
              toast(
                `已将 ${result?.patient?.display_name || member.display_name} 设为本次问诊对象。`,
              );
              // Refresh the consultation snapshot immediately, so the
              // attach panel reads the new binding without waiting for
              // the next consultation round-trip.
              options.onBound?.(result);
            } catch (error) {
              toast(error.message);
            } finally {
              trigger.disabled = false;
            }
          },
        );
        if (!consultation.sessionId) {
          bind.disabled = true;
          bind.title = "请先开始一次问诊";
        }
        controls.append(bind);
      }
      if (!archived) {
        controls.append(
          action("改名", "secondary-button", () => {
            const editor = node("div", "member-rename");
            const input = textInput("", { maxLength: 80 });
            input.value = member.display_name;
            input.setAttribute("aria-label", "成员新名称");
            editor.append(
              input,
              action("保存", "primary-button", async () => {
                try {
                  await api(`/api/patients/${member.patient_id}`, {
                    method: "PATCH",
                    body: { display_name: input.value.trim() },
                  });
                  toast("成员名称已更新。");
                  await loadMembers();
                } catch (error) {
                  toast(error.message);
                }
              }),
            );
            card.append(editor);
          }),
        );
      }
      if (!archived && !member.is_default) {
        controls.append(
          action("归档", "text-button", async () => {
            if (
              !window.confirm("归档后该成员将置灰，不可选为问诊对象。继续吗？")
            )
              return;
            try {
              await api(`/api/patients/${member.patient_id}/archive`, {
                method: "POST",
                body: {},
              });
              toast("成员已归档。");
              await loadMembers();
            } catch (error) {
              toast(error.message);
            }
          }),
        );
      }
      card.append(controls);
      target.append(card);
    }
    if (!viewingDefaultMember()) {
      target.append(
        node(
          "p",
          "notice-strip",
          "测量记录与趋势目前只支持本人档案；当前查看的是成员档案，趋势区已隐藏。",
        ),
      );
    }
    if (!state.membersReady) {
      target.append(
        node("p", "notice-strip", "未能加载成员列表，目前仅显示本人。"),
      );
    }
    syncMeasurementAvailability();
  }

  async function addMember(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const feedback = $("#member-feedback");
    setFormBusy(form, true);
    try {
      await api("/api/patients", {
        method: "POST",
        body: { display_name: $("#member-add-name").value.trim() },
      });
      form.reset();
      toast("成员已添加。");
      await loadMembers();
    } catch (error) {
      feedback.textContent = error.message;
      toast(error.message);
    } finally {
      setFormBusy(form, false);
    }
  }

  async function loadBaseline() {
    const member = activeMember();
    state.baseline = null;
    state.baselineReady = false;
    if (!member) {
      fillBaselineForm(null);
      renderBaselineSummary();
      return;
    }
    try {
      const data = await api(`/api/patients/${member.patient_id}`);
      const value = data?.patient;
      state.baseline =
        value && BASELINE_FIELDS.some((key) => value[key] !== undefined)
          ? value
          : null;
    } catch {
      state.baseline = null;
    }
    state.baselineReady = true;
    fillBaselineForm(state.baseline);
    renderBaselineSummary();
    const feedback = $("#baseline-feedback");
    if (feedback && !state.membersReady && !state.baseline)
      feedback.textContent = "未能加载基线信息；表单可填写，保存后请留意提示。";
  }

  async function saveBaseline(event) {
    event.preventDefault();
    const member = activeMember();
    const form = event.currentTarget;
    const feedback = $("#baseline-feedback");
    if (!member) {
      feedback.textContent = "请先确认当前成员。";
      return;
    }
    const payload = collectBaseline();
    setFormBusy(form, true);
    try {
      const saved = await api(`/api/patients/${member.patient_id}`, {
        method: "PATCH",
        body: payload,
      });
      state.baseline =
        saved?.patient && typeof saved.patient === "object"
          ? saved.patient
          : payload;
      fillBaselineForm(state.baseline);
      renderBaselineSummary();
      feedback.textContent = "基线信息已保存。";
      toast("基线信息已保存。");
    } catch (error) {
      feedback.textContent = error.message;
      toast(error.message);
    } finally {
      setFormBusy(form, false);
    }
  }

  async function loadTimeline() {
    const member = activeMember();
    if (!member || member.patient_id === state.defaultPatient?.patient_id)
      return api("/api/patient/timeline");
    try {
      return await api(`/api/patients/${member.patient_id}/timeline`);
    } catch {
      toast("未能加载该成员的时间线，暂不展示默认档案内容。");
      return { entries: [] };
    }
  }

  async function refreshTimelineForActiveMember() {
    state.timeline = await loadTimeline();
    renderTimeline();
  }

  function renderMeasurementValues(kindDef) {
    const target = $("#measurement-record-values");
    target.replaceChildren();
    if (kindDef.dual) {
      const systolic = textInput("measurement-record-systolic", {
        type: "number",
      });
      systolic.inputMode = "decimal";
      systolic.required = true;
      const diastolic = textInput("measurement-record-diastolic", {
        type: "number",
      });
      diastolic.inputMode = "decimal";
      diastolic.required = true;
      const unit = node("select");
      unit.id = "measurement-record-unit";
      for (const unitValue of kindDef.units) {
        const option = node("option", "", unitValue);
        option.value = unitValue;
        unit.append(option);
      }
      target.append(
        fieldRow("收缩压", systolic),
        fieldRow("舒张压", diastolic),
        fieldRow("单位", unit),
      );
      return;
    }
    const valueInput = textInput("measurement-record-value", {
      type: "number",
    });
    valueInput.inputMode = "decimal";
    valueInput.required = true;
    const unit = node("select");
    unit.id = "measurement-record-unit";
    for (const unitValue of kindDef.units) {
      const option = node("option", "", unitValue);
      option.value = unitValue;
      if (unitValue === kindDef.defaultUnit) option.selected = true;
      unit.append(option);
    }
    target.append(
      fieldRow(`${kindDef.label}数值`, valueInput),
      fieldRow("单位", unit),
    );
  }

  function buildMeasurementDialog() {
    const dialog = node("dialog", "small-dialog");
    dialog.id = "measurement-record-dialog";
    dialog.setAttribute("aria-labelledby", "measurement-record-title");
    const form = node("form", "");
    form.id = "measurement-record-form";
    const heading = node("div", "dialog-heading");
    heading.append(node("h2", "", "记录一次测量"));
    heading.lastChild.id = "measurement-record-title";
    const kindSelect = node("select");
    kindSelect.id = "measurement-record-kind";
    for (const kindDef of MEASUREMENT_KINDS) {
      const option = node("option", "", kindDef.label);
      option.value = kindDef.kind;
      kindSelect.append(option);
    }
    kindSelect.addEventListener("change", () => {
      const kindDef =
        MEASUREMENT_KINDS.find((item) => item.kind === kindSelect.value) ||
        MEASUREMENT_KINDS[0];
      renderMeasurementValues(kindDef);
      const contextWrap = $("#measurement-record-context-wrap");
      if (contextWrap) contextWrap.hidden = kindDef.kind !== "blood_glucose";
    });
    const values = node("div", "");
    values.id = "measurement-record-values";
    const time = textInput("measurement-record-time", {
      type: "datetime-local",
    });
    time.required = true;
    const contextWrap = node("div", "");
    contextWrap.id = "measurement-record-context-wrap";
    const context = node("select");
    context.id = "measurement-record-context";
    for (const [value, label] of [
      ["fasting", "空腹"],
      ["before_meal", "餐前"],
      ["after_meal_1h", "餐后 1 小时"],
      ["after_meal_2h", "餐后 2 小时"],
      ["bedtime", "睡前"],
      ["random", "随机"],
      ["unknown", "暂不确定"],
    ]) {
      const option = node("option", "", label);
      option.value = value;
      context.append(option);
    }
    contextWrap.append(fieldRow("测量场景", context));
    const feedback = node("p", "form-feedback", "");
    feedback.id = "measurement-record-feedback";
    feedback.setAttribute("role", "status");
    const footer = node("div", "dialog-footer");
    const cancel = node("button", "secondary-button close-dialog", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", () => dialog.close());
    const submit = node("button", "primary-button", "保存记录");
    submit.type = "submit";
    footer.append(cancel, submit);
    form.append(
      heading,
      node(
        "p",
        "dialog-description",
        "保存原始数值、单位与测量时间；趋势区只展示数值统计，不做判断。",
      ),
      fieldRow("测量种类", kindSelect),
      values,
      fieldRow("测量时间", time),
      contextWrap,
      feedback,
      footer,
    );
    form.addEventListener("submit", submitMeasurement);
    dialog.append(form);
    return dialog;
  }

  function openMeasurementDialog() {
    // M2 defence in depth: the button is already disabled for
    // non-default members; this stops any other opener path too.
    if (!viewingDefaultMember()) {
      toast("测量记录目前只支持本人档案");
      return;
    }
    ensureRecordsSections();
    const dialog = $("#measurement-record-dialog");
    $("#measurement-record-feedback").textContent = "";
    const kindDef = MEASUREMENT_KINDS[0];
    $("#measurement-record-kind").value = kindDef.kind;
    renderMeasurementValues(kindDef);
    $("#measurement-record-context-wrap").hidden = false;
    $("#measurement-record-time").value = localDateInput();
    dialog.showModal();
    $("#measurement-record-value")?.focus();
  }

  async function submitMeasurement(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const feedback = $("#measurement-record-feedback");
    if (!viewingDefaultMember()) {
      feedback.textContent = "测量记录目前只支持本人档案";
      return;
    }
    const kindValue = $("#measurement-record-kind").value;
    const kindDef =
      MEASUREMENT_KINDS.find((item) => item.kind === kindValue) ||
      MEASUREMENT_KINDS[0];
    let body;
    if (kindDef.dual) {
      body = {
        systolic: $("#measurement-record-systolic").value,
        diastolic: $("#measurement-record-diastolic").value,
        unit: $("#measurement-record-unit").value,
        measured_at: isoTime($("#measurement-record-time").value, true),
      };
    } else {
      body = {
        value_text: $("#measurement-record-value").value,
        unit: $("#measurement-record-unit").value,
        measured_at: isoTime($("#measurement-record-time").value, true),
      };
      if (kindDef.kind === "blood_glucose")
        body.measurement_context = $("#measurement-record-context").value;
    }
    const endpoint = kindDef.legacy
      ? "/api/patient/measurements/blood-glucose"
      : `/api/patient/measurements/${kindDef.slug}`;
    setFormBusy(form, true);
    try {
      await api(endpoint, { method: "POST", body });
      form.reset();
      $("#measurement-record-dialog").close();
      await refresh();
      toast("测量数据已按测量时间保存。");
    } catch (error) {
      feedback.textContent = error.message;
      toast(error.message);
    } finally {
      setFormBusy(form, false);
    }
  }

  function renderMeasurementTrends() {
    const section = $("#records-measurement-trends");
    const list = $("#measurement-trend-list");
    if (!section || !list) return;
    list.replaceChildren();
    let shown = 0;
    for (const kindDef of MEASUREMENT_KINDS) {
      if (kindDef.legacy) continue;
      const trend = state.measurementTrends[kindDef.kind];
      if (!trend?.summary) continue;
      shown += 1;
      const card = node("article", "measurement-trend-card");
      card.append(
        node("h3", "", `${kindDef.label}趋势 · ${trend.count} 次已确认记录`),
        node(
          "p",
          "",
          // Blood pressure has no single average: its summary carries
          // systolic/diastolic sub-summaries instead.
          kindDef.dual && trend.summary.systolic
            ? `收缩压平均 ${trend.summary.systolic.average}，范围 ${trend.summary.systolic.minimum}–${trend.summary.systolic.maximum} ${kindDef.defaultUnit}；舒张压平均 ${trend.summary.diastolic.average}，范围 ${trend.summary.diastolic.minimum}–${trend.summary.diastolic.maximum} ${kindDef.defaultUnit}；最近一次 ${trend.summary.latest.reported_value} ${kindDef.defaultUnit} · ${formatTime(trend.summary.latest.measured_at)}。仅展示描述性变化，不判断高低。`
            : `平均 ${trend.summary.average}，范围 ${trend.summary.minimum}–${trend.summary.maximum} ${kindDef.defaultUnit}；最近一次 ${trend.summary.latest.normalized_value} ${kindDef.defaultUnit} · ${formatTime(trend.summary.latest.measured_at)}。仅展示描述性变化，不判断高低。`,
        ),
      );
      list.append(card);
    }
    section.hidden = shown === 0;
  }

  async function loadMeasurementTrends() {
    const entries = await Promise.all(
      MEASUREMENT_KINDS.filter((item) => !item.legacy).map(async (kindDef) => {
        try {
          return [
            kindDef.kind,
            await api(`/api/patient/trends/${kindDef.slug}`),
          ];
        } catch {
          return [kindDef.kind, null];
        }
      }),
    );
    state.measurementTrends = Object.fromEntries(entries);
    renderMeasurementTrends();
  }

  async function refresh() {
    if (!state.initialized) await ensurePatient();
    [state.timeline, state.glucoseTrend] = await Promise.all([
      loadTimeline(),
      api("/api/patient/trends/blood-glucose"),
    ]);
    renderTimeline();
    renderGlucoseTrend();
    ensureRecordsSections();
    await loadMembers().catch(() => {});
    await loadBaseline().catch(() => {});
    await loadMeasurementTrends().catch(() => {});
    // loadMembers already syncs via renderMembers; this final pass
    // covers the trend data that only arrived just above.
    syncMeasurementAvailability();
  }

  function observationEditor(observation, detail, rerender) {
    const form = node("form", "record-inline-edit");
    const valueLabel = node("label", "field");
    valueLabel.append(node("span", "", "当前内容"));
    const input = node("input");
    input.required = true;
    input.maxLength = 300;
    input.value = observation.value_text || "";
    valueLabel.append(input);
    const unitLabel = node("label", "field");
    unitLabel.append(node("span", "", "单位（可选）"));
    const unit = node("input");
    unit.maxLength = 40;
    unit.value = observation.unit || "";
    unitLabel.append(unit);
    const submit = node("button", "primary-button", "保存修改");
    submit.type = "submit";
    form.append(valueLabel, unitLabel, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setFormBusy(form, true);
      try {
        const updated = await api(
          `/api/observations/${observation.observation_id}/revisions`,
          {
            method: "POST",
            body: {
              value_text: input.value,
              unit: unit.value,
              expected_revision_id: observation.current_revision_id,
            },
          },
        );
        toast("修改已保存，原始识别和旧版本仍可查看。");
        await refresh();
        rerender(updated);
      } catch (error) {
        toast(error.message);
        if (error.code === "stale_patient_record")
          await openDetail(detail.entry.entry_id);
      } finally {
        setFormBusy(form, false);
      }
    });
    return form;
  }

  function observationCard(observation, detail, rerender) {
    const card = node("article", "record-observation");
    const head = node("div", "record-observation-head");
    head.append(
      node("h4", "", observation.display_name),
      node(
        "span",
        `subtle-pill ${observation.verification_status === "needs_confirmation" ? "record-pending" : ""}`,
        verification[observation.verification_status] ||
          observation.verification_status,
      ),
    );
    card.append(head);
    if (observation.kind !== "document_content") {
      card.append(
        node(
          "p",
          "record-observation-value",
          observation.value_text
            ? `${observation.value_text}${observation.unit ? ` ${observation.unit}` : ""}`
            : "尚未确认数值",
        ),
      );
    }
    if (observation.context)
      card.append(node("p", "", `识别原文：${observation.context}`));
    const pending = observation.clarifications.find(
      (item) => item.status === "pending",
    );
    if (pending) {
      card.append(node("p", "", pending.reason));
      if (pending.alternatives.length) {
        const alternatives = node("div", "record-alternatives");
        for (const candidate of pending.alternatives) {
          alternatives.append(
            action(candidate, "secondary-button", async () => {
              try {
                const updated = await api(
                  `/api/clarifications/${pending.clarification_id}/answers`,
                  {
                    method: "POST",
                    body: {
                      value_text: candidate,
                      unit: observation.unit || "",
                      expected_revision_id: observation.current_revision_id,
                    },
                  },
                );
                toast("这项模糊内容已经更新。");
                await refresh();
                rerender(updated);
              } catch (error) {
                toast(error.message);
              }
            }),
          );
        }
        card.append(alternatives);
      }
    }
    const controls = node("div", "record-observation-actions");
    // P2b: only patient-confirmed/corrected transcriptions may be
    // saved as measurements; the server re-checks saveability and
    // refuses an inferred unit until the patient confirms it.
    if (
      observation.kind === "document_number" &&
      ["patient_confirmed", "patient_corrected"].includes(
        observation.verification_status,
      )
    ) {
      controls.append(
        action("另存为测量", "secondary-button", async (event) => {
          const trigger = event.currentTarget;
          trigger.disabled = true;
          try {
            let saved = await api(
              "/api/patient/measurements/from-observation",
              {
                method: "POST",
                body: {
                  entry_id: detail.entry.entry_id,
                  observation_id: observation.observation_id,
                },
              },
            );
            if (saved.requires_confirmation) {
              const confirmed = window.confirm(
                `单位未标明，按 ${saved.unit} 保存？`,
              );
              if (!confirmed) return;
              saved = await api("/api/patient/measurements/from-observation", {
                method: "POST",
                body: {
                  entry_id: detail.entry.entry_id,
                  observation_id: observation.observation_id,
                  unit: saved.unit,
                },
              });
            }
            toast("已另存为测量。");
            await refresh();
            rerender(await api(`/api/entries/${detail.entry.entry_id}`));
          } catch (error) {
            toast(error.message);
          } finally {
            trigger.disabled = false;
          }
        }),
      );
    }
    if (observation.verification_status === "model_extracted") {
      controls.append(
        action("确认无误", "secondary-button", async (event) => {
          const trigger = event.currentTarget;
          trigger.disabled = true;
          try {
            const updated = await api(
              `/api/observations/${observation.observation_id}/revisions`,
              {
                method: "POST",
                body: {
                  value_text: observation.value_text,
                  unit: observation.unit || "",
                  expected_revision_id: observation.current_revision_id,
                  reason: "confirmation",
                },
              },
            );
            toast("已确认这项识别内容。");
            await refresh();
            rerender(updated);
          } catch (error) {
            toast(error.message);
            trigger.disabled = false;
          }
        }),
      );
    }
    const edit = action(
      pending ? "手动填写" : "编辑",
      "secondary-button",
      () => {
        edit.hidden = true;
        card.append(observationEditor(observation, detail, rerender));
        card.querySelector(".record-inline-edit input")?.focus();
      },
    );
    controls.append(edit);
    card.append(controls);
    if (observation.revisions.length > 1) {
      const history = node("details", "record-history");
      history.append(
        node("summary", "", `${observation.revisions.length} 个版本`),
      );
      const list = node("ul");
      for (const revision of observation.revisions) {
        list.append(
          node(
            "li",
            "",
            `${formatTime(revision.created_at)} · ${revision.author_type === "patient" ? "患者修改" : "模型识别"} · ${revision.value_text || "未识别"}${revision.unit ? ` ${revision.unit}` : ""}`,
          ),
        );
      }
      history.append(list);
      card.append(history);
    }
    return card;
  }

  function consultationPanel(detail) {
    const panel = node("section", "record-consultation-panel");
    const copy = node("div");
    copy.append(node("h3", "", "用于本次问诊"));
    const verified = detail.observations.filter(
      (item) =>
        ["patient_confirmed", "patient_corrected"].includes(
          item.verification_status,
        ) && item.value_text,
    );
    const context = options.getConsultationContext?.() || {};
    const attached = context.reportItems?.some(
      (item) => item.source_entry_id === detail.entry.entry_id,
    );
    // P2d gate mirror (backend attach gate stays authoritative): a bound
    // session admits only the bound member's entries; an unbound
    // self-consultation admits only the default patient's entries.
    // Unknown owner/default ids fail open to the backend gate, which
    // still rejects mismatches with its own message.
    const entryPatientId = detail.entry.patient_id;
    const boundPatientId = context.boundPatientId || null;
    const defaultPatientId = state.defaultPatient?.patient_id || null;
    const entryMatchesSubject = boundPatientId
      ? entryPatientId === boundPatientId
      : context.subject === "self" &&
        (!entryPatientId ||
          !defaultPatientId ||
          entryPatientId === defaultPatientId);
    let message = "只会带入你已经确认的数据，并保留测量时间和来源。";
    if (!verified.length)
      message = "先核对识别内容；未经你确认的数据不会进入问诊。";
    else if (!context.sessionId)
      message =
        "先开始一次问诊，再选择要使用的档案（本人或已绑定的家庭成员）。";
    else if (boundPatientId && !entryMatchesSubject)
      message = "这份档案不属于本次问诊对象，为避免混淆不能带入。";
    else if (!boundPatientId && context.subject && context.subject !== "self")
      message = "本次问诊对象还没有绑定家庭成员档案，暂不能带入他人记录。";
    else if (
      !boundPatientId &&
      context.subject === "self" &&
      !entryMatchesSubject
    )
      message = "这份档案不属于本人，为避免混淆不能带入。";
    else if (!context.subject && !boundPatientId)
      message = "请先在问诊中确认咨询对象，再带入这份档案。";
    copy.append(node("p", "", message));
    panel.append(copy);
    if (verified.length && context.sessionId && entryMatchesSubject) {
      const attach = action(
        attached ? "更新本次问诊" : `带入 ${verified.length} 项数据`,
        "primary-button",
        async (event) => {
          const trigger = event.currentTarget;
          trigger.disabled = true;
          try {
            const result = await api(
              `/api/sessions/${context.sessionId}/patient-records/${detail.entry.entry_id}/attach`,
              { method: "POST", body: {} },
            );
            $("#record-detail-dialog").close();
            options.onAttached?.(result);
          } catch (error) {
            toast(error.message);
            trigger.disabled = false;
          }
        },
      );
      panel.append(attach);
    } else if (options.openConsultation) {
      panel.append(
        action("前往问诊", "secondary-button", () => {
          $("#record-detail-dialog").close();
          options.openConsultation();
        }),
      );
    }
    return panel;
  }

  function renderDetail(detail) {
    const entry = detail.entry;
    $("#record-detail-title").textContent = entry.title;
    const target = $("#record-detail-content");
    target.replaceChildren();
    const asset = detail.assets[0];
    if (asset) {
      const image = node("img", "record-source-image");
      image.src = `/api/assets/${asset.asset_id}/content`;
      image.alt = `${entry.title}原始图片`;
      target.append(image);
    }
    const meta = node("div", "record-detail-meta");
    meta.append(
      node(
        "span",
        "",
        entry.entry_type === "manual_measurement"
          ? "患者手动录入"
          : "患者上传图片",
      ),
      node("span", "", `测量：${formatTime(entry.measured_at)}`),
      node("span", "", `上传：${formatTime(entry.uploaded_at)}`),
      node("span", "", statuses[entry.status] || entry.status),
    );
    target.append(meta);
    const recognition = [...detail.recognitions]
      .reverse()
      .find((item) => item.status === "succeeded");
    if (recognition?.result?.content) {
      const description = node("section", "record-description");
      description.append(
        node("h3", "", "图片整体内容"),
        node(
          "p",
          "",
          recognition.result.content.description || "没有形成可靠的整体描述。",
        ),
      );
      target.append(description);
    } else if (entry.entry_type === "uploaded_document") {
      target.append(
        node(
          "p",
          "notice-strip",
          entry.status === "recognition_failed"
            ? "图片已归档，但本次识别没有完成。你可以保留原图，稍后重新识别。"
            : "图片已经归档，当前还没有识别结果。",
        ),
      );
    }
    const observations = detail.observations;
    if (observations.length) {
      const section = node("section", "record-observations");
      section.append(node("h3", "", "数值与记录"));
      const list = node("div", "record-observation-list");
      const rerender = (updated) => renderDetail(updated);
      for (const observation of observations)
        list.append(observationCard(observation, detail, rerender));
      section.append(list);
      target.append(section);
    }
    target.append(consultationPanel(detail));
    const actions = node("div", "record-detail-actions");
    if (
      asset &&
      state.visionAvailable &&
      ["awaiting_recognition", "recognition_failed"].includes(entry.status)
    ) {
      actions.append(
        action("重新识别图片", "secondary-button", async (event) => {
          const trigger = event.currentTarget;
          trigger.disabled = true;
          try {
            const updated = await api(
              `/api/assets/${asset.asset_id}/recognitions`,
              {
                method: "POST",
                body: {},
              },
            );
            await refresh();
            renderDetail(updated);
          } catch (error) {
            toast(error.message);
            trigger.disabled = false;
          }
        }),
      );
    }
    actions.append(
      action("删除这条记录", "text-button record-delete", async () => {
        if (
          !window.confirm(
            "删除后原始图片和这条记录将从本机档案中移除。继续吗？",
          )
        )
          return;
        try {
          await api(`/api/entries/${entry.entry_id}`, {
            method: "DELETE",
            body: { expected_version: entry.version },
          });
          $("#record-detail-dialog").close();
          await refresh();
          toast("这条健康记录已删除。");
        } catch (error) {
          toast(error.message);
        }
      }),
    );
    target.append(actions);
  }

  async function openDetail(entryId) {
    const detail = await api(`/api/entries/${entryId}`);
    renderDetail(detail);
    $("#record-detail-dialog").showModal();
  }

  async function uploadImage(event) {
    event.preventDefault();
    if (state.busy) return;
    const form = event.currentTarget;
    const file = $("#medical-image-file").files[0];
    if (!file) return;
    if (file.size > 4 * 1024 * 1024) {
      $("#medical-image-feedback").textContent = "图片不能超过 4 MB。";
      return;
    }
    state.busy = true;
    setFormBusy(form, true);
    $("#medical-image-feedback").textContent = "正在安全归档图片…";
    try {
      if (!state.initialized) await ensurePatient();
      const uploaded = await api("/api/patient/entries/images", {
        method: "POST",
        body: {
          title: $("#medical-image-name").value,
          measured_at: isoTime($("#medical-image-time").value),
          reported_at: isoTime($("#medical-report-time").value),
          filename: file.name,
          data_url: await readFile(file),
        },
      });
      let detail = uploaded;
      if (state.visionAvailable) {
        $("#medical-image-feedback").textContent = "正在识别数值和整体内容…";
        try {
          detail = await api(`/api/assets/${uploaded.asset_id}/recognitions`, {
            method: "POST",
            body: {},
          });
        } catch (error) {
          toast(`图片已归档；${error.message}`);
        }
      } else {
        toast("图片已归档。配置支持图片的 DeepSeek 模型后可以继续识别。");
      }
      $("#medical-image-dialog").close();
      form.reset();
      $("#medical-image-preview").hidden = true;
      await refresh();
      if (detail.entry) await openDetail(detail.entry.entry_id);
    } catch (error) {
      $("#medical-image-feedback").textContent = error.message;
    } finally {
      state.busy = false;
      setFormBusy(form, false);
    }
  }

  async function recordGlucose(event) {
    event.preventDefault();
    if (state.busy) return;
    const form = event.currentTarget;
    state.busy = true;
    setFormBusy(form, true);
    try {
      if (!state.initialized) await ensurePatient();
      await api("/api/patient/measurements/blood-glucose", {
        method: "POST",
        body: {
          value_text: $("#glucose-record-value").value,
          unit: $("#glucose-record-unit").value,
          measured_at: isoTime($("#glucose-record-time").value, true),
          measurement_context: $("#glucose-record-context").value,
        },
      });
      form.reset();
      $("#glucose-record-dialog").close();
      await refresh();
      toast("血糖数据已按测量时间保存。");
    } catch (error) {
      $("#glucose-record-feedback").textContent = error.message;
    } finally {
      state.busy = false;
      setFormBusy(form, false);
    }
  }

  function bind() {
    ensureRecordsSections();
    document.querySelectorAll("[data-record-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        state.filter = button.dataset.recordFilter;
        document.querySelectorAll("[data-record-filter]").forEach((item) => {
          const active = item === button;
          item.classList.toggle("active", active);
          item.setAttribute("aria-pressed", String(active));
        });
        renderTimeline();
      });
    });
    $("#upload-medical-image").addEventListener("click", () => {
      $("#medical-image-feedback").textContent = "";
      $("#medical-image-dialog").showModal();
      $("#medical-image-file").focus();
    });
    $("#add-glucose").addEventListener("click", () => {
      $("#glucose-record-feedback").textContent = "";
      $("#glucose-record-time").value = localDateInput();
      $("#glucose-record-dialog").showModal();
      $("#glucose-record-value").focus();
    });
    $("#medical-image-file").addEventListener("change", async (event) => {
      if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
      const file = event.target.files[0];
      const preview = $("#medical-image-preview");
      const fileLabel = $("#medical-image-file-label");
      if (!file) {
        preview.hidden = true;
        fileLabel.textContent = "尚未选择文件";
        return;
      }
      fileLabel.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
      state.previewUrl = URL.createObjectURL(file);
      preview.src = state.previewUrl;
      preview.hidden = false;
    });
    $("#medical-image-form").addEventListener("submit", uploadImage);
    $("#glucose-record-form").addEventListener("submit", recordGlucose);
    $("#medical-image-dialog").addEventListener("close", () => {
      if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
      state.previewUrl = null;
      $("#medical-image-preview").removeAttribute("src");
      $("#medical-image-file-label").textContent = "尚未选择文件";
    });
  }

  bind();
  return { refresh, openDetail };
}
