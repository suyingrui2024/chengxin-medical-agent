// Registered inputs only. Model text never determines controls or executable code.
import { renderBodyMap } from "./visual-question.js";

const labels = {
  region: "部位",
  area: "具体位置",
  side: "左右",
  extent: "范围",
};
function node(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
export function validateLocationAnswerSpec(spec) {
  if (
    !spec ||
    spec.schema !== "location-answer/v1" ||
    typeof spec.id !== "string" ||
    !/^[0-9a-f-]{36}$/.test(spec.id) ||
    !Array.isArray(spec.fields) ||
    spec.fields[0] !== "region" ||
    new Set(spec.fields).size !== spec.fields.length ||
    spec.fields.some((x) => !Object.hasOwn(labels, x))
  )
    throw new Error("Invalid location answer contract");
  const validateOptions = (options) => {
    if (!Array.isArray(options) || options.length > 16)
      throw new Error("Invalid location options");
    for (const option of options)
      if (
        typeof option.value !== "string" ||
        !/^[a-z][a-z0-9_]{0,40}$/.test(option.value) ||
        typeof option.label !== "string" ||
        !option.label ||
        option.label.length > 40
      )
        throw new Error("Invalid location option");
  };
  validateOptions(spec.regions);
  validateOptions(spec.side_options);
  validateOptions(spec.extent_options);
  if (!spec.area_presets || typeof spec.area_presets !== "object")
    throw new Error("Invalid area presets");
  for (const [region, options] of Object.entries(spec.area_presets)) {
    if (!spec.regions.some((x) => x.value === region))
      throw new Error("Unknown area region");
    validateOptions(options);
  }
  if (
    spec.prefill &&
    (typeof spec.prefill.id !== "string" ||
      typeof spec.prefill.evidence !== "string" ||
      spec.prefill.evidence.length > 1000 ||
      !spec.prefill.answers ||
      typeof spec.prefill.answers !== "object")
  )
    throw new Error("Invalid location draft");
}

export function renderLocationChips(answers) {
  const chips = node("div", "location-answer-chips");
  for (const field of Object.keys(labels)) {
    const answer = answers?.[field];
    if (!answer || typeof answer.label !== "string") continue;
    chips.append(
      node(
        "span",
        `location-answer-chip ${answer.assertion === "unknown" ? "uncertain" : ""}`,
        `${labels[field]} · ${answer.label}`,
      ),
    );
  }
  return chips;
}

export function renderLocationAnswer(data, context) {
  const spec = data.answer_spec;
  validateLocationAnswerSpec(spec);
  const root = node("section", "location-answer");
  root.setAttribute("aria-label", "逐项核对不适位置");
  const token = `${spec.id}:${spec.prefill?.id || "new"}`;
  const prior = context.taskDraft?.locationAnswer;
  const restored = prior?.token === token;
  const answers = structuredClone(
    restored ? prior.answers : spec.prefill?.answers || {},
  );
  let note = restored ? prior.note || "" : "";
  const answered = (field) =>
    field === "area"
      ? Boolean(
          answers.area &&
          (answers.area.kind === "unknown" ||
            (typeof answers.area.value === "string" &&
              answers.area.value.trim())),
        )
      : typeof answers[field] === "string" && Boolean(answers[field]);
  const firstMissing = () => {
    const index = spec.fields.findIndex((field) => !answered(field));
    return index < 0 ? spec.fields.length : index;
  };
  let stage =
    restored && Number.isInteger(prior.stage)
      ? Math.max(0, Math.min(prior.stage, spec.fields.length + 1))
      : firstMissing();
  let generation = 0;
  const actionable =
    !context.readOnly && typeof context.onConfirmLocation === "function";
  const save = () =>
    context.onTaskDraft?.({
      ...(context.taskDraft || {}),
      locationAnswer: { token, answers: structuredClone(answers), note, stage },
    });
  const describe = (field) => {
    const value = answers[field];
    if (!answered(field)) return "未填写";
    if (field === "area")
      return value.kind === "unknown"
        ? "说不清"
        : value.kind === "text"
          ? value.value
          : spec.area_presets[answers.region]?.find(
              (x) => x.value === value.value,
            )?.label || "未填写";
    const options =
      field === "region" ? spec.regions : spec[`${field}_options`];
    return options.find((x) => x.value === value)?.label || "未填写";
  };
  function draw() {
    const epoch = ++generation;
    const live = () =>
      actionable && epoch === generation && !context.isBusy?.();
    const button = (text, fn, cls = "") => {
      const e = node("button", `location-answer-control ${cls}`, text);
      e.type = "button";
      e.dataset.ready = String(actionable);
      e.disabled = !actionable || Boolean(context.isBusy?.());
      e.addEventListener("click", () => {
        if (live()) fn();
      });
      return e;
    };
    root.replaceChildren();
    const pane = root.closest(".active-task");
    if (pane) {
      pane.scrollTop = 0;
      requestAnimationFrame(() => {
        if (epoch === generation && !context.isBusy?.()) pane.scrollTop = 0;
      });
    }
    const progress = node("div", "location-answer-progress");
    progress.append(
      node(
        "span",
        "",
        stage < spec.fields.length
          ? `${stage + 1} / ${spec.fields.length} · ${labels[spec.fields[stage]]}`
          : stage === spec.fields.length
            ? "核对位置"
            : "补充位置细节",
      ),
    );
    if (spec.prefill) {
      const source = node("details", "location-answer-source");
      source.append(
        node("summary", "", "原话整理 · 待核对"),
        node("blockquote", "", spec.prefill.evidence),
      );
      progress.append(source);
    }
    root.append(progress);
    const next = () => {
      if (answered(spec.fields[stage])) {
        stage++;
        save();
        draw();
      }
    };
    if (stage > spec.fields.length) {
      const input = node("textarea", "location-answer-note");
      input.maxLength = 200;
      input.value = note;
      input.setAttribute("aria-label", "定位补充");
      input.disabled = !actionable;
      input.dataset.ready = String(actionable);
      input.addEventListener("input", () => {
        if (live()) {
          note = input.value;
          save();
        }
      });
      root.append(
        input,
        button(
          "返回核对",
          () => {
            stage = spec.fields.length;
            save();
            draw();
          },
          "primary-button",
        ),
      );
      return;
    }
    if (stage === spec.fields.length) {
      const review = node("div", "location-answer-review");
      for (const field of spec.fields)
        review.append(
          button(
            `${labels[field]} · ${describe(field)}`,
            () => {
              stage = spec.fields.indexOf(field);
              save();
              draw();
            },
            "location-answer-review-field",
          ),
        );
      root.append(review);
      const confirm = button(
        "核对并记录位置",
        () =>
          context.onConfirmLocation({
            schema: spec.schema,
            form_id: spec.id,
            answers: structuredClone(answers),
            note,
          }),
        "primary-button location-answer-confirm",
      );
      confirm.dataset.ready = String(
        actionable && firstMissing() === spec.fields.length,
      );
      confirm.disabled =
        !actionable ||
        firstMissing() !== spec.fields.length ||
        Boolean(context.isBusy?.());
      const actions = node("div", "location-answer-final-actions");
      actions.append(
        button(note ? "补充 · 已填" : "补充（可选）", () => {
          stage = spec.fields.length + 1;
          save();
          draw();
        }),
        confirm,
      );
      root.append(actions);
      return;
    }
    const field = spec.fields[stage];
    if (field === "region") {
      root.append(
        renderBodyMap(
          { ...data, result_proposal: undefined },
          {
            ...context,
            readOnly: !actionable,
            isBusy: () => !live(),
            includeOtherRegion: true,
            regionConfirmLabel: "下一步",
            taskDraft: { location: answers.region },
            onTaskDraft: (draft) => {
              if (live()) {
                if (answers.region !== draft.location) delete answers.area;
                answers.region = draft.location;
                save();
              }
            },
            onChoose: (_label, value) => {
              if (live()) {
                if (answers.region !== value) delete answers.area;
                answers.region = value;
                next();
              }
            },
          },
        ),
      );
      return;
    }
    const panel = node("div", `location-answer-field field-${field}`);
    const customArea = field === "area" && answers.area?.kind === "text";
    if (field === "area" && answers.region === "head" && !customArea) {
      panel.classList.add("with-area-figure");
      const picture = node("figure", "location-area-figure");
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 160 160");
      svg.setAttribute("aria-hidden", "true");
      const area = answers.area?.kind === "preset" ? answers.area.value : null;
      const back = area === "occiput";
      const marks = {
        forehead: '<path d="M43 47Q80 31 117 47L115 65Q80 52 45 65Z"/>',
        crown: '<path d="M44 39Q80 8 116 39L108 49Q80 28 52 49Z"/>',
        occiput: '<ellipse cx="80" cy="76" rx="31" ry="29"/>',
        temple:
          '<ellipse cx="42" cy="75" rx="8" ry="14"/><ellipse cx="118" cy="75" rx="8" ry="14"/>',
      };
      svg.innerHTML =
        '<path d="M41 63C36 25 59 17 80 17S124 25 119 63C134 56 133 87 120 89C118 121 98 138 80 138S42 121 40 89C27 87 26 56 41 63Z" fill="#e3e9e2" stroke="#a2baa6" stroke-width="2"/>' +
        (!back
          ? '<g fill="none" stroke="#8ca18e" stroke-width="2"><path d="M53 70h17m20 0h17M80 74v17h7M66 110q14 8 28 0"/></g>'
          : "") +
        `<g fill="#80a88b" opacity="0.65">${marks[area] || ""}</g>`;
      picture.append(
        svg,
        node("figcaption", "", back ? "后面 · 区域示意" : "前面 · 区域示意"),
      );
      panel.append(picture);
    }
    const choices = node("div", "location-answer-options");
    choices.setAttribute("role", "group");
    choices.setAttribute("aria-label", labels[field]);
    if (field === "side")
      panel.append(node("p", "location-answer-hint", "按你自己的左右选择"));
    if (field === "extent")
      panel.append(
        node("p", "location-answer-hint", "只描述感觉范围，不测量大小"),
      );
    let options =
      field === "area"
        ? spec.area_presets[answers.region] || []
        : spec[`${field}_options`];
    options = [...options];
    if (field === "area")
      options.push(
        { value: "describe", label: "自己描述" },
        { value: "unknown", label: "说不清" },
      );
    if (customArea && spec.area_presets[answers.region]?.length)
      options = [
        { value: "back_to_presets", label: "按图选" },
        { value: "unknown", label: "说不清" },
      ];
    for (const option of options) {
      const current =
        field === "area"
          ? answers.area?.kind === "preset"
            ? answers.area.value
            : answers.area?.kind === "unknown"
              ? "unknown"
              : answers.area?.kind === "text"
                ? "describe"
                : null
          : answers[field];
      const control = button(
        option.label,
        () => {
          if (option.value === "back_to_presets") {
            delete answers.area;
            save();
            draw();
            return;
          }
          if (field === "area")
            answers.area =
              option.value === "unknown"
                ? { kind: "unknown", value: null }
                : option.value === "describe"
                  ? {
                      kind: "text",
                      value:
                        answers.area?.kind === "text" ? answers.area.value : "",
                    }
                  : { kind: "preset", value: option.value };
          else answers[field] = option.value;
          save();
          draw();
        },
        "location-answer-choice",
      );
      control.setAttribute("aria-pressed", String(current === option.value));
      if (field === "extent" && option.value !== "unknown") {
        const symbol = node(
          "span",
          `extent-symbol ${option.value}`,
          option.value === "point" ? "•" : "●",
        );
        symbol.setAttribute("aria-hidden", "true");
        control.prepend(symbol);
      }
      choices.append(control);
    }
    panel.append(choices);
    if (
      field === "area" &&
      (!spec.area_presets[answers.region]?.length ||
        answers.area?.kind === "text")
    ) {
      const input = node("input", "location-area-input");
      input.type = "text";
      input.maxLength = 120;
      input.value = answers.area?.kind === "text" ? answers.area.value : "";
      input.placeholder = "更具体的位置";
      input.setAttribute("aria-label", "描述具体位置");
      input.disabled = !actionable;
      input.dataset.ready = String(actionable);
      input.addEventListener("input", () => {
        if (live()) {
          answers.area = { kind: "text", value: input.value };
          choices
            .querySelectorAll("button")
            .forEach((e) => e.setAttribute("aria-pressed", "false"));
          save();
          updateNext();
        }
      });
      panel.append(input);
    }
    root.append(panel);
    const nav = node("div", "location-answer-navigation");
    nav.append(
      button("上一步", () => {
        stage--;
        save();
        draw();
      }),
    );
    const forward = button("下一步", next, "primary-button");
    function updateNext() {
      forward.dataset.ready = String(actionable && answered(field));
      forward.disabled =
        !actionable || !answered(field) || Boolean(context.isBusy?.());
    }
    updateNext();
    nav.append(forward);
    root.append(nav);
  }
  draw();
  return root;
}
