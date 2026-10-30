// Structured task / adaptive choice families.
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

export function installTask(register, taskProgress) {
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
        const viewerId = `illustration-title-${++illustrationSequence.value}`;
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
      // Risk level rides on the card itself, not on the route section
      // below. The route is only rendered when no conclusion sheet is
      // present (the sheet owns D1/D5), so keying the emphasis off it made
      // the alarming reading lose its colour in exactly the case that
      // escalates — the reading that closed the session. `risk_level` is
      // always in the payload, so the record can carry it either way.
      const riskLevel = ["emergency", "urgent", "routine"].includes(
        data.risk_level,
      )
        ? data.risk_level
        : "routine";
      card.dataset.riskLevel = riskLevel;
      // The closing guidance leads the card: what to do next and which
      // changes need immediate help must not sit below the full fact list.
      // When a conclusion sheet is present it owns D1/D5, and this card
      // renders in record mode (design: 结论单 §7 de-duplication).
      if (!context.conclusionPresent) {
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
}
