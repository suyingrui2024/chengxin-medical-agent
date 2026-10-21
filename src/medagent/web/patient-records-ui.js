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

export function createPatientRecordsUI(api, toast, options = {}) {
  const state = {
    initialized: false,
    visionAvailable: false,
    timeline: null,
    glucoseTrend: null,
    previewUrl: null,
    busy: false,
    filter: "all",
  };

  function setFormBusy(form, busy) {
    for (const control of form.elements) control.disabled = busy;
  }

  async function ensurePatient() {
    const catalog = await api("/api/patient");
    state.initialized = true;
    state.visionAvailable = catalog.vision_available;
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

  async function refresh() {
    if (!state.initialized) await ensurePatient();
    [state.timeline, state.glucoseTrend] = await Promise.all([
      api("/api/patient/timeline"),
      api("/api/patient/trends/blood-glucose"),
    ]);
    renderTimeline();
    renderGlucoseTrend();
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
    let message = "只会带入你已经确认的数据，并保留测量时间和来源。";
    if (!verified.length)
      message = "先核对识别内容；未经你确认的数据不会进入问诊。";
    else if (!context.sessionId)
      message = "先开始一次问诊并确认咨询对象为本人，再选择要使用的档案。";
    else if (context.subject && context.subject !== "self")
      message = "当前问诊对象不是本人，为避免混淆，这份档案不能带入。";
    else if (!context.subject)
      message = "请先在问诊中确认咨询对象为本人，再带入这份档案。";
    copy.append(node("p", "", message));
    panel.append(copy);
    if (verified.length && context.sessionId && context.subject === "self") {
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
      if (!file) {
        preview.hidden = true;
        return;
      }
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
    });
  }

  bind();
  return { refresh, openDetail };
}
