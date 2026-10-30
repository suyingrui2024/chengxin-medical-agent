// Records detail: observations, recognition, interpretation and binding.
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

export function installRecordsDetail({
  api,
  toast,
  options,
  state,
  refresh,
  setFormBusy,
  ensurePatient,
  renderTimeline,
  ensureRecordsSections,
}) {
  // P3c 识别轮询状态（单条目、单定时器；上限见 RECOGNITION_POLL）。
  let recognitionPollTimer = null;
  let recognitionPollEntryId = null;
  let recognitionPollAttempts = 0;
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
    const pair = collapsePair("带入本次问诊");
    panel.append(pair.toggle, pair.body);
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
    pair.body.append(copy);
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
      pair.body.append(attach);
    } else if (options.openConsultation) {
      pair.body.append(
        action("前往问诊", "secondary-button", () => {
          $("#record-detail-dialog").close();
          options.openConsultation();
        }),
      );
    }
    return panel;
  }

  function stopRecognitionPoll() {
    if (recognitionPollTimer !== null) {
      clearTimeout(recognitionPollTimer);
      recognitionPollTimer = null;
    }
    recognitionPollEntryId = null;
    recognitionPollAttempts = 0;
  }

  // P3c：轮询一条 recognizing 条目，直到状态离开 recognizing、
  // 失败或达到上限；完成时刷新时间线并重绘详情（对话框已关闭时
  // 只刷新时间线）。同一条目不重复起轮询。
  function startRecognitionPoll(entryId) {
    if (recognitionPollEntryId === entryId && recognitionPollTimer !== null)
      return;
    stopRecognitionPoll();
    recognitionPollEntryId = entryId;
    const tick = async () => {
      recognitionPollTimer = null;
      recognitionPollAttempts += 1;
      try {
        const fresh = await api(`/api/entries/${entryId}`);
        if (fresh.entry?.status !== "recognizing") {
          stopRecognitionPoll();
          await refresh().catch(() => {});
          if ($("#record-detail-dialog")?.open) renderDetail(fresh);
          return;
        }
      } catch (error) {
        stopRecognitionPoll();
        toast(error.message);
        return;
      }
      if (recognitionPollAttempts >= RECOGNITION_POLL.maxAttempts) {
        stopRecognitionPoll();
        toast("识别仍在进行中，稍后可在档案时间线中查看。");
        return;
      }
      recognitionPollTimer = setTimeout(tick, RECOGNITION_POLL.intervalMs);
    };
    recognitionPollTimer = setTimeout(tick, RECOGNITION_POLL.intervalMs);
  }

  async function loadInterpretation(entryId) {
    try {
      const data = await api(`/api/patients/entries/${entryId}/interpretation`);
      if (!data) return null;
      return data.interpretation ?? (data.items || data.summary ? data : null);
    } catch {
      // 无解读（404）与接口未就绪都先按“无解读”呈现；
      // 开始解读按钮的 POST 失败会如实提示，不伪造解读。
      return null;
    }
  }

  // P3a：往详情里填「解读」节。已有解读则完整呈现；无解读时
  // 显示“尚未解读”，仅当有已确认观测时才给“开始解读”按钮
  // （设计 §5.1：未确认值不进解读）；POST 失败只提示、不伪造。
  async function loadInterpretationInto(section, detail, onInterpretation) {
    const entry = detail.entry;
    const loading = node("p", "", "正在加载解读…");
    section.append(loading);
    const interpretation = await loadInterpretation(entry.entry_id);
    loading.remove();
    if (interpretation) {
      const built = createInterpretationSection(interpretation);
      section.replaceChildren(...built.childNodes);
      onInterpretation?.(interpretation);
      return;
    }
    section.append(node("p", "", "尚未解读"));
    const confirmed = (detail.observations || []).some(
      (item) =>
        item.verification_status === "patient_confirmed" ||
        item.verification_status === "patient_corrected",
    );
    if (!confirmed) {
      section.append(
        node("p", "notice-strip", "请先确认转录数值，确认后才可生成解读。"),
      );
      return;
    }
    const button = action("开始解读", "secondary-button", async (event) => {
      const trigger = event.currentTarget;
      trigger.disabled = true;
      trigger.textContent = "正在生成解读…";
      try {
        const data = await api(
          `/api/patients/entries/${entry.entry_id}/interpretations`,
          { method: "POST", body: {} },
        );
        const created =
          data?.interpretation ?? (data?.items || data?.summary ? data : null);
        if (!created) throw new Error("解读接口尚未返回解读结果，请稍后再试。");
        const built = createInterpretationSection(created);
        section.replaceChildren(...built.childNodes);
        await refresh().catch(() => {});
      } catch (error) {
        toast(error.message || "解读功能尚未就绪，请稍后再试。");
        trigger.disabled = false;
        trigger.textContent = "开始解读";
      }
    });
    section.append(button);
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
    // P3c：识别中状态明示并轮询；已完成/失败时停掉轮询。
    if (entry.status === "recognizing") {
      target.append(
        node("p", "notice-strip", "识别中，完成后会自动刷新本页。"),
      );
      startRecognitionPoll(entry.entry_id);
    } else {
      stopRecognitionPoll();
    }
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
    // P3a：解读节与转录观测分区呈现；内容由 loadInterpretationInto
    // 异步填入（已有解读 / 尚未解读 / 开始解读三种状态）。
    const interpretationSection = node("section", "record-interpretation");
    const interpretationPair = collapsePair("解读");
    interpretationPair.toggle.classList.add("record-interpretation-toggle");
    interpretationSection.append(
      interpretationPair.toggle,
      interpretationPair.body,
    );
    target.append(interpretationSection);
    void loadInterpretationInto(
      interpretationPair.body,
      detail,
      (interpretation) => {
        // S3: the collapsed title carries the worst grade, so the
        // outcome is visible without expanding.
        const order = { 红: 3, 黄: 2, 无法判断: 1, 绿: 0 };
        let worst = null;
        for (const item of interpretation.items || []) {
          const grade = normalizeInterpretationGrade(item.grade);
          if (worst === null || (order[grade] ?? 0) > (order[worst] ?? 0))
            worst = grade;
        }
        if (worst) interpretationPair.toggle.textContent = `解读 · ${worst}`;
      },
    );
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
            // P3d async: POST now returns 202 {run_id, entry_id},
            // not the old 201 detail. Re-open by entry id so the
            // detail view's recognizing poll (GET /api/entries/{id},
            // unchanged address) starts tracking the job.
            if (updated?.entry) renderDetail(updated);
            else if (updated?.entry_id) await openDetail(updated.entry_id);
            else await openDetail(entry.entry_id);
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
      // P3d async: the 202 response carries entry_id, not a detail
      // body; open by id so the recognizing poll starts. The upload
      // response itself still carries entry as a fallback.
      const targetEntryId =
        detail?.entry?.entry_id ||
        detail?.entry_id ||
        uploaded?.entry?.entry_id;
      if (targetEntryId) await openDetail(targetEntryId);
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
  return { openDetail, bind };
}
