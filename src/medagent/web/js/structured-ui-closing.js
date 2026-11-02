// References, misc and closing card families.
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

export function installClosing(register, renderScaleResult) {
  function isWebUrl(value) {
    try {
      const link = new URL(value);
      return (
        (link.protocol === "http:" || link.protocol === "https:") &&
        !link.username &&
        !link.password
      );
    } catch {
      return false;
    }
  }

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
      if (data.narrative !== undefined && data.narrative !== null) {
        const narrative = data.narrative;
        if (typeof narrative !== "object" || Array.isArray(narrative))
          throw new Error("Invalid narrative");
        text(narrative.framing);
        if (!narrative.framing.length)
          throw new Error("Invalid narrative framing");
        text(narrative.severity);
        if (!narrative.severity.length)
          throw new Error("Invalid narrative severity");
        if (narrative.reasoning !== undefined && narrative.reasoning !== null)
          text(narrative.reasoning);
      }
      // The composer carries the hint inside disposition (its historic
      // slot); a sheet-level copy is also accepted if one is present.
      for (const hint of [disposition.department_hint, data.department_hint]) {
        if (hint !== undefined && hint !== null) {
          text(hint.department);
          text(hint.reason);
        }
      }
      if (
        data.department_visible !== undefined &&
        typeof data.department_visible !== "boolean"
      )
        throw new Error("Invalid department visibility");
      // v1.3 §13.4: the composer may add directions_mode (off |
      // restricted | on) and visit_notes (string list). Both are
      // optional — missing means pre-v1.3 behavior — but when present
      // they must match their shapes; all other strictness is
      // unchanged and unknown fields still fail elsewhere.
      if (
        data.directions_mode !== undefined &&
        data.directions_mode !== null &&
        !["off", "restricted", "on"].includes(data.directions_mode)
      )
        throw new Error("Invalid directions mode");
      if (
        data.directions_restricted_visible !== undefined &&
        typeof data.directions_restricted_visible !== "boolean"
      )
        throw new Error("Invalid restricted directions visibility");
      if (data.visit_notes !== undefined && data.visit_notes !== null)
        list(data.visit_notes).forEach(text);
      if (
        data.follow_up !== undefined &&
        data.follow_up !== null &&
        data.follow_up.due_in_hours !== undefined &&
        data.follow_up.due_in_hours !== null &&
        !Number.isFinite(data.follow_up.due_in_hours)
      )
        throw new Error("Invalid follow-up window");
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
      // Batch B: the worry slot and the goal response are validated
      // with the same text() rule as directions and gaps.
      if (data.worry !== undefined && data.worry !== null) {
        text(data.worry.object);
        text(data.worry.response);
      }
      if (data.goal_response !== undefined && data.goal_response !== null)
        text(data.goal_response);
      if (data.outlook) text(data.outlook);
      if (data.abstain_text) text(data.abstain_text);
      const reading = data.further_reading;
      if (reading) {
        if (reading.status !== undefined) text(reading.status);
        if (
          reading.sheet_revision !== undefined &&
          reading.sheet_revision !== null &&
          !Number.isFinite(reading.sheet_revision)
        )
          throw new Error("Invalid further-reading revision");
        if (
          reading.sources_version !== undefined &&
          reading.sources_version !== null
        )
          text(reading.sources_version);
        if (reading.citations !== undefined)
          list(reading.citations).forEach((citation) => {
            text(citation.title);
            text(citation.url);
            text(citation.publisher);
            if (citation.published_at) text(citation.published_at);
            text(citation.fetched_at);
            if (!["T1", "T2"].includes(citation.tier))
              throw new Error("Invalid citation tier");
            text(citation.quote);
          });
      }
    },
    (data, context = {}) => {
      const card = el("article", "conclusion-card");
      // S2 visualization simplification: secondary sections collapse
      // behind a titled toggle with a count. Expanded content is
      // unchanged; only its disclosure state changes.
      const collapse = (title, count, section, expanded) => {
        const wrap = el("section", "conclusion-collapse");
        const toggle = button(
          count ? `${title} · ${count}` : title,
          () => {
            body.hidden = !body.hidden;
            toggle.setAttribute(
              "aria-expanded",
              body.hidden ? "false" : "true",
            );
          },
          "conclusion-collapse-toggle",
        );
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        const body = el("div", "conclusion-collapse-body");
        body.hidden = !expanded;
        body.append(section);
        wrap.append(toggle, body);
        return wrap;
      };
      card.append(el("p", "conclusion-kicker", "问诊结论"));
      // v1.3 §13.6 渲染单源契约：结论卡只消费 composer 的 flag，
      // 不重算可见性，不以“字段在场”推断可见。方向模式取自
      // composer 的 directions_mode；缺失该字段的旧 sheet 按
      // composer 的 flag 语义回落：directions_visible 为真即全
      // 量（其含义不变），否则 directions_restricted_visible
      // 为真即受限，否则 off。科室行仅当 department_visible
      // === true 时输出，缺失即不可见。framing / severity /
      // worry / goal_response 不走任何门（患者自己的问题与
      // 处置级文本恒渲染）。
      const directionsMode =
        data.directions_mode === "restricted" ||
        data.directions_mode === "on" ||
        data.directions_mode === "off"
          ? data.directions_mode
          : data.directions_visible === true
            ? "on"
            : data.directions_restricted_visible === true
              ? "restricted"
              : "off";
      const you = data.subject_kind === "self" ? "你" : "您";
      // §13.1 段 1：一句话判断（framing＋severity）。
      if (data.narrative) {
        const narrative = el("section", "conclusion-narrative");
        narrative.append(
          el("p", "conclusion-narrative-framing", data.narrative.framing),
        );
        const severity = el(
          "p",
          `conclusion-narrative-severity ${data.disposition.level}`,
        );
        severity.append(
          el("span", `conclusion-level-dot ${data.disposition.level}`),
          data.narrative.severity,
        );
        narrative.append(severity);
        // Reasoning quotes direction labels verbatim, so the reasoning
        // paragraph renders only in full mode (C3 sign-off gate); in
        // restricted mode the direction labels stay out of the
        // reasoning line even though framing/severity always render.
        if (data.narrative.reasoning && directionsMode === "on") {
          const reasoning = el("div", "conclusion-narrative-reasoning");
          reasoning.append(
            el("p", "conclusion-narrative-reasoning-label", "为什么这样判断"),
            el(
              "p",
              "conclusion-narrative-reasoning-text",
              data.narrative.reasoning,
            ),
          );
          narrative.append(reasoning);
        }
        card.append(narrative);
      }
      // Batch B: when the sheet carries the patient's named worry,
      // answer it in its own expanded section, quoting the sheet's
      // response verbatim. The object is rendered as-is (no marker
      // reconstructed around it); direction/department visibility
      // gates do not apply here because this slot is the patient's
      // own question, not a direction or a department hint.
      if (data.worry) {
        const worry = el("section", "conclusion-worry");
        worry.append(
          el("h4", "", "你问过的担心"),
          el("p", "conclusion-worry-object", `你问过：${data.worry.object}`),
          el("p", "conclusion-worry-response", data.worry.response),
        );
        card.append(worry);
      }
      // Batch B R3: the goal response gets its own expanded section
      // beside the worry section, quoting the sheet's response
      // verbatim. The response already quotes the goal itself, so no
      // object line is reconstructed here. The same no-gate rule as
      // worry applies: this answers the patient's own goal, not a
      // direction or a department hint. An empty goal response
      // renders nothing at all, not an empty shell.
      if (data.goal_response) {
        const goal = el("section", "conclusion-goal");
        goal.append(
          el("h4", "", "你问到的目标"),
          el("p", "conclusion-goal-response", data.goal_response),
        );
        card.append(goal);
      }
      // §13.1 段 3：可能方向。off 模式整段不渲染；全量模式保持
      // 既有顺序与行为（likely 在前、弃权文案、折叠收起）；受限
      // 模式按 composer 顺序渲染、rule_out 组在前且直接展开——
      // 不得以“排序靠后”或“折叠默认收起”弱化需要排除的方向；
      // 方向列表为空时整段不渲染（不渲染弃权文案、不渲染空壳）。
      const directionTemplates = {
        more_likely: (label) => `更像是 ${label}`,
        possible: (label) => `可能是 ${label}`,
        cannot_exclude: (label) =>
          `目前还不能排除 ${label}，这也是建议就诊的原因`,
      };
      const renderDirectionBox = (directions, item) => {
        const box = el("div", `conclusion-direction ${item.group}`);
        box.append(
          el(
            "strong",
            "",
            directionTemplates[item.certainty](item.patient_label),
          ),
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
      };
      if (directionsMode === "on") {
        const directions = el("section", "conclusion-directions");
        directions.append(el("h4", "", "可能的方向（不是确诊）"));
        if (data.directions_abstained) {
          directions.append(el("p", "conclusion-abstain", data.abstain_text));
        } else {
          for (const group of ["likely", "rule_out"]) {
            const items = (data.directions || []).filter(
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
            for (const item of items) renderDirectionBox(directions, item);
          }
        }
        const directionCount = directions.querySelectorAll(
          ".conclusion-direction",
        ).length;
        card.append(
          collapse("可能的方向（不是确诊）", directionCount, directions, false),
        );
      } else if (directionsMode === "restricted") {
        const items = (data.directions || []).filter(
          (item) => item.patient_label,
        );
        if (items.length) {
          const directions = el("section", "conclusion-directions");
          directions.append(el("h4", "", "可能的方向（不是确诊）"));
          for (const group of ["rule_out", "likely"]) {
            const groupItems = items.filter((item) => item.group === group);
            if (!groupItems.length) continue;
            directions.append(
              el(
                "p",
                "conclusion-direction-group",
                group === "likely" ? "更可能的方向" : "需要排除的方向",
              ),
            );
            for (const item of groupItems) renderDirectionBox(directions, item);
          }
          card.append(directions);
        }
      }
      // §13.1 段 4：就医建议。disposition.text 本身已是单源窗口句
      // （composer 的 format_window_text 把窗口写进文本），不再
      // 另起一行写窗口，避免出现两份去向文本；科室行仅当
      // department_visible === true 时输出。
      const disposition = el(
        "section",
        `conclusion-disposition ${data.disposition.level}`,
      );
      if (data.tier === "urgent") disposition.setAttribute("role", "alert");
      disposition.append(
        el("p", "conclusion-disposition-label", "下一步"),
        el("p", "conclusion-disposition-text", data.disposition.text),
      );
      const departmentHint =
        data.disposition.department_hint || data.department_hint;
      if (data.department_visible === true && departmentHint)
        disposition.append(
          el(
            "p",
            "conclusion-department",
            `建议科室：${departmentHint.department} —— ${departmentHint.reason}`,
          ),
        );
      card.append(disposition);
      // §13.1 段 5：就诊带话清单。有项才渲染；段标题按
      // subject_kind 适配（本人／家人）。
      if (data.visit_notes?.length) {
        const notes = el("section", "conclusion-visit-notes");
        notes.append(
          el(
            "h4",
            "",
            data.subject_kind === "self"
              ? "就诊时请告诉医生"
              : "就诊时请帮家人告诉医生",
          ),
        );
        const notesList = el("ul");
        data.visit_notes.forEach((item) =>
          notesList.append(el("li", "", item)),
        );
        notes.append(notesList);
        card.append(notes);
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
        card.append(collapse("还缺的信息", data.gaps.length, gaps, false));
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
        // P2c: the archive allergy hint is its own line below the
        // avoid list, never merged into it.
        if (care.allergy_hint)
          careEl.append(
            el("p", "conclusion-care-allergy-hint", care.allergy_hint),
          );
        const careCount =
          (care.do?.length || 0) +
          (care.avoid?.length || 0) +
          (care.measure?.length || 0);
        card.append(collapse("现在能做的事", careCount, careEl, false));
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
        // Urgent dispositions keep the safety net expanded: there it is
        // the primary action, not secondary information.
        card.append(
          collapse(
            "出现以下变化，请立即求助",
            data.return_now_if.length,
            safety,
            data.disposition.level === "urgent_today",
          ),
        );
      }
      if (data.outlook || data.follow_up) {
        const outlook = el("section", "conclusion-outlook");
        outlook.append(el("h4", "", "接下来"));
        if (data.outlook)
          outlook.append(
            el(
              "p",
              "",
              /[。！？]$/.test(data.outlook)
                ? data.outlook
                : `${data.outlook}。`,
            ),
          );
        if (data.follow_up?.due_in_hours)
          outlook.append(
            el(
              "p",
              "",
              `如果到 ${data.follow_up.due_in_hours} 小时后还没有好转，或出现上面任何警示信号，请线下就医，不要等随访。`,
            ),
          );
        const outlookCount = (data.outlook ? 1 : 0) + (data.follow_up ? 1 : 0);
        card.append(collapse("接下来", outlookCount, outlook, false));
      }
      // §13.1 段 10：安抚收尾＋提醒约定。收尾句是 §13.5 静态
      // 模板、按处置层级取用，恒渲染。提醒约定落到 §13.5(a)：
      // 关闭时已自动排定可通知随访（sheet.follow_up 即该计划），
      // 渲染信息行＋按钮；按钮经 context 回调聚焦既有随访卡，
      // 是真实动作。无回调时不渲染按钮——绝不出现点击无实际
      // 效果的按钮。无随访计划时不渲染提醒行、不编造约定。
      const closing = el("section", "conclusion-closing");
      closing.append(
        el(
          "p",
          "conclusion-closing-line",
          data.disposition.level === "urgent_today"
            ? "请按上面的时间尽快就诊，不要等随访。"
            : "先别太焦虑，按上面的安排来就好；有变化随时回来问。",
        ),
      );
      const reminderHours = data.follow_up?.due_in_hours;
      if (Number.isFinite(reminderHours) && reminderHours > 0) {
        closing.append(
          el(
            "p",
            "conclusion-reminder-info",
            `我会在 ${reminderHours} 小时后来确认`,
          ),
        );
        if (typeof context.onFocusFollowUpCard === "function")
          closing.append(
            button(
              "查看随访安排",
              () => context.onFocusFollowUpCard(),
              "conclusion-reminder-action",
            ),
          );
      }
      card.append(closing);
      const citations = (data.further_reading?.citations || []).filter(
        (citation) => isWebUrl(citation.url),
      );
      if (citations.length) {
        const reading = el("section", "conclusion-reading");
        reading.append(el("h4", "", "延伸阅读（来自权威来源）"));
        reading.append(
          el(
            "p",
            "conclusion-reading-note",
            "以下内容来自网络公开资料，未经本系统临床评审，仅供了解参考。",
          ),
        );
        const readingList = el("ul");
        citations.forEach((citation) => {
          const li = el("li");
          const link = el("a", "", citation.title);
          link.href = citation.url;
          link.target = "_blank";
          link.rel = "noopener";
          li.append(link);
          li.append(
            el(
              "p",
              "conclusion-reading-source",
              citation.published_at
                ? `${citation.publisher} · ${citation.published_at}`
                : `${citation.publisher} · 发布日期未标注`,
            ),
          );
          if (citation.quote)
            li.append(el("p", "conclusion-reading-quote", citation.quote));
          readingList.append(li);
        });
        reading.append(readingList);
        card.append(collapse("延伸阅读", citations.length, reading, false));
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
      if (data.due_at !== undefined && data.due_at !== null) text(data.due_at);
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
      if (data.reason) card.append(el("p", "followup-reason", data.reason));
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
      if (data.result_renderer === SCALE_RENDERER) {
        if (!Number.isInteger(data.max_score) || data.max_score < 1)
          throw new Error("Invalid scale max score");
        if (!data.disclaimer?.trim())
          throw new Error("Missing scale disclaimer");
        if (
          !data.band ||
          typeof data.band !== "object" ||
          Array.isArray(data.band)
        )
          throw new Error("Invalid scale band");
        for (const key of ["key", "label", "range"])
          if (data.band[key] !== undefined) text(data.band[key]);
        const scaleItems = list(data.items);
        if (!scaleItems.length || scaleItems.length > 60)
          throw new Error("Invalid scale items");
        for (const item of scaleItems) {
          text(item.id);
          text(item.label);
          text(item.answer_label);
          if (!Number.isInteger(item.score))
            throw new Error("Invalid scale item score");
        }
      }
      if (data.result_renderer === FORMULA_RENDERER) {
        if (!data.disclaimer?.trim())
          throw new Error("Missing formula disclaimer");
        if (
          !data.band ||
          typeof data.band !== "object" ||
          Array.isArray(data.band)
        )
          throw new Error("Invalid formula band");
        for (const key of ["key", "label", "range"])
          if (data.band[key] !== undefined) text(data.band[key]);
      }
      if (
        !Object.hasOwn(inputStates, data.input_status) ||
        !Object.hasOwn(qualityNames, data.quality)
      )
        throw new Error("Invalid quality");
    },
    (data, context) => {
      if (data.result_renderer === SCALE_RENDERER)
        return renderScaleResult(data, context);
      const card = el("article", "calculation-card formula-result");
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
      // A formula may declare score bands; render them as a neutral chip.
      // No red/green colouring: a band is not a verdict.
      if (data.band?.label) card.append(el("p", "scale-band", data.band.label));
      if (data.disclaimer)
        card.append(el("p", "scale-disclaimer", data.disclaimer));
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
}
