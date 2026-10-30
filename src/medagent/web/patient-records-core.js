// Records core: state, summary/timeline, baseline and member management.
import {
  $,
  node,
  action,
  collapsePair,
  icon,
  localDateInput,
  isoTime,
  dateParts,
  formatTime,
  readFile,
  statuses,
  verification,
  INTERPRETATION_DISCLAIMER,
  INTERPRETATION_GRADES,
  normalizeInterpretationGrade,
  RECOGNITION_POLL,
  createInterpretationSection,
  BASELINE_FIELDS,
  MEASUREMENT_KINDS,
  ALLERGY_KINDS,
  createBaselineBanner,
  splitList,
} from "./patient-records-primitives.js";

export function installRecordsCore({ api, toast, options, deps }) {
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
    // S3: the notice strip only appears when something needs attention
    // (unconfirmed items, unclear values, or a failed recognition).
    const notice = $("#records-notice");
    if (notice)
      notice.hidden = !entries.some(
        (item) =>
          item.review_count > 0 ||
          item.pending_count > 0 ||
          ["awaiting_recognition", "recognition_failed"].includes(item.status),
      );
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
        deps.openDetail(item.entry_id).catch((error) => toast(error.message)),
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
      const statusChildren = [
        node(
          "span",
          `subtle-pill ${item.review_count ? "record-pending" : ""}`,
          item.pending_count
            ? `${item.pending_count} 项内容模糊`
            : item.review_count
              ? `${item.review_count} 项待确认`
              : statuses[item.status] || item.status,
        ),
      ];
      // P3a：只有后端明确给出已解读标志时才加徽标，无标志不猜测。
      if (
        item.has_interpretation === true ||
        item.interpreted === true ||
        item.interpretation_status === "ready" ||
        item.interpretation_status === "completed"
      )
        statusChildren.push(node("span", "subtle-pill", "已解读"));
      statusChildren.push(icon("arrow"));
      status.append(...statusChildren);
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
    const pair = collapsePair(
      `近期血糖 · ${trend.count} 次已确认记录 · 最近 ${trend.summary.latest.normalized_value} mmol/L · ${formatTime(trend.summary.latest.measured_at)}`,
    );
    pair.body.append(copy, visual);
    target.append(pair.toggle, pair.body);
    target.hidden = false;
  }

  function activeMember() {
    return (
      state.members.find((item) => item.patient_id === state.activePatientId) ||
      state.defaultPatient
    );
  }

  // P2 follow-up: member routes exist now, so this helper only
  // answers "is the viewed member the default profile" (used for
  // legacy default-route selection in measurementApiBase).
  function viewingDefaultMember() {
    const member = activeMember();
    return Boolean(
      member &&
      state.defaultPatient &&
      member.patient_id === state.defaultPatient.patient_id,
    );
  }

  // P2 follow-up (P3/P4 batch): measurement and trend routes are
  // per-member now. The default member keeps the legacy /api/patient
  // routes; any other viewed member uses /api/patients/{id}/... with
  // the same body shapes, so writes land under the viewed member.
  function measurementApiBase() {
    const member = activeMember();
    if (!member || member.patient_id === state.defaultPatient?.patient_id)
      return "/api/patient";
    return `/api/patients/${member.patient_id}`;
  }

  function syncMeasurementAvailability() {
    // Measurements are available for every viewed member (per-member
    // routes above); the button is never disabled for being a member.
    const button = $("#add-measurement");
    if (button) {
      button.disabled = false;
      button.title = "";
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
    // S3: baseline form folds into "我的基线"; the toggle status text is
    // refreshed by renderBaselineSummary below.
    const baselinePair = collapsePair("我的基线");
    baselinePair.toggle.id = "baseline-collapse-toggle";
    baseline.querySelector("h2")?.replaceWith(baselinePair.toggle);
    while (baseline.children.length > 1)
      baselinePair.body.append(baseline.children[1]);
    baseline.append(baselinePair.body);

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
    // S3: member list folds into "家庭成员 · N" (refreshed by renderMembers).
    const membersPair = collapsePair("家庭成员");
    membersPair.toggle.id = "members-collapse-toggle";
    members.querySelector("h2")?.replaceWith(membersPair.toggle);
    while (members.children.length > 1)
      membersPair.body.append(members.children[1]);
    members.append(membersPair.body);

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
    // S3: per-kind trend list folds into "自测趋势 · N 种".
    const trendsPair = collapsePair("自测趋势");
    trendsPair.toggle.id = "trends-collapse-toggle";
    trends.querySelector("h2")?.replaceWith(trendsPair.toggle);
    while (trends.children.length > 1)
      trendsPair.body.append(trends.children[1]);
    trends.append(trendsPair.body);

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
    const toggle = $("#baseline-collapse-toggle");
    if (toggle)
      toggle.textContent = !value
        ? "我的基线 · 未填写"
        : parts.length
          ? "我的基线 · 已填写"
          : "我的基线 · 已保存";
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
    const toggle = $("#members-collapse-toggle");
    if (toggle) toggle.textContent = `家庭成员 · ${state.members.length}`;
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
    // Member switch must also move the trends off the default
    // profile; otherwise a member view would keep showing self data.
    try {
      state.glucoseTrend = await api(
        `${measurementApiBase()}/trends/blood-glucose`,
      );
      renderGlucoseTrend();
    } catch {
      /* trends stay as they were; the timeline above is authoritative */
    }
    await loadMeasurementTrends().catch(() => {});
    syncMeasurementAvailability();
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
    const endpoint = `${measurementApiBase()}/measurements/${kindDef.slug}`;
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
      const latestText = kindDef.dual
        ? `${trend.summary.latest.reported_value} ${kindDef.defaultUnit}`
        : `${trend.summary.latest.normalized_value} ${kindDef.defaultUnit}`;
      const pair = collapsePair(
        `${kindDef.label}趋势 · ${trend.count} 次 · 最近 ${latestText} · ${formatTime(trend.summary.latest.measured_at)}`,
      );
      pair.body.append(
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
      card.append(pair.toggle, pair.body);
      list.append(card);
    }
    section.hidden = shown === 0;
    const toggle = $("#trends-collapse-toggle");
    if (toggle) toggle.textContent = `自测趋势 · ${shown} 种`;
  }

  async function loadMeasurementTrends() {
    const base = measurementApiBase();
    const entries = await Promise.all(
      MEASUREMENT_KINDS.filter((item) => !item.legacy).map(async (kindDef) => {
        try {
          return [kindDef.kind, await api(`${base}/trends/${kindDef.slug}`)];
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
      api(`${measurementApiBase()}/trends/blood-glucose`),
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

  return {
    state,
    refresh,
    setFormBusy,
    ensurePatient,
    renderTimeline,
    ensureRecordsSections,
  };
}
