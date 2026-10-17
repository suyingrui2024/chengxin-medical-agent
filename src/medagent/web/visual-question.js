// First-party visual vocabulary. Model output supplies text/data, never SVG or code.
const ns = "http://www.w3.org/2000/svg";
const paths = {
  expand: ['<path d="M6 19V6h13M29 6h13v13M42 29v13H29M19 42H6V29"/>'],
  person: [
    '<circle cx="24" cy="14" r="7"/>',
    '<path d="M10 41v-7c0-13 28-13 28 0v7M18 30v11M30 30v11"/>',
  ],
  child: [
    '<circle cx="24" cy="17" r="8"/>',
    '<path d="M18 10c3-7 9-4 9-1M14 41v-8c0-9 20-9 20 0v8M20 38h8"/>',
  ],
  family: [
    '<circle cx="17" cy="15" r="6"/><circle cx="34" cy="19" r="5"/>',
    '<path d="M5 40v-7c0-12 24-12 24 0v7M31 29c9-2 13 2 13 7v4"/>',
  ],
  calendar: [
    '<rect x="8" y="10" width="32" height="32" rx="5"/>',
    '<path d="M16 5v10M32 5v10M8 22h32M17 29h3M28 29h3M17 35h3"/>',
  ],
  chat: ['<path d="M7 8h34v25H22L10 42v-9H7zM15 17h18M15 24h12"/>'],
  clock: [
    '<circle cx="24" cy="25" r="17"/>',
    '<path d="M24 14v12l9 6M18 4h12"/>',
  ],
  up: ['<path d="M6 37h36M9 29l11-8 9 4 11-16M29 9h11v11"/>'],
  down: ['<path d="M6 37h36M9 10l11 8 9-4 11 16M29 30h11V19"/>'],
  stable: ['<path d="M6 37h36M8 23h32M32 15l8 8-8 8"/>'],
  unknown: [
    '<circle cx="24" cy="24" r="18"/>',
    '<path d="M18 18c0-8 13-8 13 0 0 5-7 5-7 10M24 34v1"/>',
  ],
  activity: [
    '<circle cx="28" cy="8" r="4"/>',
    '<path d="m17 24 7-9 8 10 9 1M24 15l-3 15-10 11M21 30l11 5 2 8M9 13h7M5 20h7"/>',
  ],
  medicine: [
    '<path d="m11 25 14-14c11-11 24 2 13 13L24 38C13 49 0 36 11 25ZM18 18l13 13"/>',
  ],
  shield: [
    '<path d="M24 5 40 12v13c0 10-16 18-16 18S8 35 8 25V12zM24 15v12M24 33v1"/>',
  ],
  breath: [
    '<path d="M24 5v20M24 17c-7-12-17-7-17 9v11c0 7 11 2 17-4 6 6 17 11 17 4V26c0-16-10-21-17-9M24 25l-9 6M24 25l9 6"/>',
  ],
  water: [
    '<path d="M24 5C18 15 10 22 10 30a14 14 0 0 0 28 0c0-8-8-15-14-25ZM17 31c0 4 3 7 7 7"/>',
  ],
  food: [
    '<path d="M10 5v14c0 7 12 7 12 0V5M16 5v38M36 43V5c-10 5-10 22 0 22"/>',
  ],
  sleep: [
    '<path d="M6 38V18M6 31h36v7M12 31V21h23c5 0 7 3 7 10M12 21v-7h10v7"/>',
  ],
  note: [
    '<rect x="10" y="7" width="28" height="36" rx="4"/>',
    '<path d="M18 4h12v8H18zM17 21h14M17 28h14M17 35h8"/>',
  ],
  heart: ['<path d="M24 41 8 26C-5 12 14-1 24 13 34-1 53 12 40 26z"/>'],
  alert: ['<path d="m24 5 21 36H3zM24 18v11M24 34v1"/>'],
  wake: [
    '<path d="M8 30c0-23 32-23 32 0v10H8zM15 23h6M29 23h6M20 33h8M7 7l6 5M41 7l-6 5"/>',
  ],
  pulse: ['<path d="M3 25h10l5-15 11 29 6-14h10"/>'],
  blood: [
    '<path d="M24 4C15 17 9 24 9 31a15 15 0 0 0 30 0c0-7-6-14-15-27ZM24 21v12M18 27h12"/>',
  ],
};

export function visualIcon(name) {
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 48 48");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2.2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  // Only constant, repository-owned paths are parsed. Never interpolate data.
  svg.innerHTML = (paths[name] || paths.chat).join("");
  return svg;
}

const visuals = {
  consult_for: {
    title: "这次，为谁咨询？",
    icon: "family",
    hint: "选择一位，本次只记录这位患者的情况。",
  },
  age: {
    title: "患者多大了？",
    icon: "calendar",
    hint: "婴幼儿请写月龄，例如 8 个月；其他人可写 32 岁。",
  },
  chief_complaint: {
    title: "最困扰你的是什么？",
    icon: "chat",
    hint: "先说一个主要问题，其他细节我们慢慢补充。",
  },
  emergency_screen: {
    title: "现在，有以下任一情况吗？",
    icon: "alert",
    hint: "请逐项查看；有任一项或不确定，都请如实选择。",
  },
  onset: {
    title: "从什么时候开始？",
    icon: "clock",
    cues: [
      ["calendar", "开始时间"],
      ["pulse", "突然 / 逐渐"],
    ],
  },
  trajectory: {
    title: "和刚开始比，怎么样了？",
    icon: "activity",
    hint: "选择最接近当前情况的一项。",
  },
  severity: {
    title: "现在，大约有多难受？",
    icon: "activity",
    hint: "也可以描述对吃饭、睡觉和活动的影响。",
  },
  associated_symptoms: {
    title: "还有其他不舒服吗？",
    icon: "person",
    hint: "同时出现的症状也请告诉我们；没有就说没有。",
  },
  resp_breathing: {
    title: "现在呼吸怎么样？",
    icon: "breath",
    cues: [
      ["breath", "气短 / 喘"],
      ["chat", "说完整句话费力？"],
      ["person", "静坐也呼吸困难？"],
    ],
  },
  resp_cough_details: {
    title: "咳嗽是什么样的？",
    icon: "breath",
    cues: [
      ["breath", "干咳 / 有痰"],
      ["blood", "咳血 / 胸痛"],
      ["sleep", "夜间加重？"],
    ],
    hint: "没有咳嗽，可以直接说没有。",
  },
  resp_intake: {
    title: "喝水、吃饭和排尿正常吗？",
    icon: "water",
    cues: [
      ["water", "饮水"],
      ["food", "进食"],
      ["note", "排尿"],
    ],
    hint: "若明显减少，请描述减少到什么程度。",
  },
  medications_taken: {
    title: "用过药，或做过什么处理？",
    icon: "medicine",
    cues: [
      ["medicine", "药名 / 处理"],
      ["note", "剂量"],
      ["clock", "时间"],
    ],
    hint: "没用过药、没做处理，可以直接说没有。",
  },
  allergies: {
    title: "有已知的药物或食物过敏吗？",
    icon: "shield",
    hint: "“没有”与“不知道”不同，请按实际情况回答。",
  },
  relevant_history: {
    title: "有哪些需要了解的病史？",
    icon: "note",
    cues: [
      ["medicine", "正在治疗的疾病"],
      ["note", "近期手术 / 相关病史"],
    ],
    hint: "没有请说没有；不清楚可以说不知道。",
  },
  patient_goal: {
    title: "你最希望得到什么帮助？",
    icon: "heart",
    hint: "想解决的问题，或你现在最担心的事。",
  },
};
const choiceIcons = {
  self: "person",
  child: "child",
  other_adult: "family",
  worsening: "up",
  improving: "down",
  stable: "stable",
  unknown: "unknown",
  yes: "alert",
  no: "shield",
};

function node(tag, cls, value) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (value !== undefined) e.textContent = value;
  return e;
}

export function validateQuestion(data) {
  if (
    typeof data.question_id !== "string" ||
    typeof data.prompt !== "string" ||
    data.prompt.length > 16000 ||
    typeof data.canonical !== "boolean" ||
    !Array.isArray(data.choices) ||
    data.choices.length > 16
  )
    throw new Error("Invalid question");
  data.choices.forEach((item) => {
    if (
      typeof item.label !== "string" ||
      item.label.length > 200 ||
      typeof item.value !== "string" ||
      item.value.length > 200
    )
      throw new Error("Invalid choice");
  });
}

export function renderQuestion(data, context) {
  // A clarification must keep its actual question visible, without canonical shortcuts.
  const spec = data.canonical ? visuals[data.question_id] : null;
  const card = node(
    "article",
    `visual-question ${data.question_id === "emergency_screen" ? "screen-question" : ""}`,
  );
  const badge = node("div", "question-symbol");
  badge.append(visualIcon(spec?.icon || "chat"));
  const heading = node("h2", "question-title", spec?.title || data.prompt);
  card.append(badge, heading);
  if (spec?.hint) card.append(node("p", "question-hint", spec.hint));
  if (spec?.cues) {
    const cues = node("div", "question-cues");
    spec.cues.forEach(([icon, label]) => {
      const cue = node("div", "question-cue");
      cue.append(visualIcon(icon), node("span", "", label));
      cues.append(cue);
    });
    card.append(cues);
  }
  if (data.question_id === "emergency_screen" && spec) {
    const signals = node("ul", "signal-grid");
    [
      ["wake", "叫不醒 / 意识明显异常"],
      ["pulse", "抽搐"],
      ["person", "嘴唇发紫"],
      ["breath", "严重呼吸困难"],
      ["heart", "持续压榨样胸痛"],
      ["blood", "大出血"],
    ].forEach(([icon, label]) => {
      const item = node("li");
      item.append(visualIcon(icon), node("span", "", label));
      signals.append(item);
    });
    card.append(signals);
  }
  if (data.question_id === "severity" && spec) {
    const scale = node("div", "severity-scale");
    const toolbar = node("div", "severity-selection");
    const display = node("output", "severity-value", "—");
    display.setAttribute("aria-live", "polite");
    display.setAttribute("aria-label", "待确认的不适程度");
    const confirm = node("button", "primary-button severity-confirm");
    confirm.type = "button";
    const group = node("div", "severity-options");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "不适程度，0 到 10 分");
    let selected =
      Number.isInteger(context.questionDraft?.degree) &&
      context.questionDraft.degree >= 0 &&
      context.questionDraft.degree <= 10
        ? context.questionDraft.degree
        : null;
    const actionable =
      !context.readOnly && typeof context.onConfirmAnswer === "function";
    const update = () => {
      display.textContent = selected === null ? "—" : `${selected} 分`;
      confirm.textContent =
        selected === null ? "先选择分数" : `确认 ${selected} 分`;
      confirm.dataset.ready = String(actionable && selected !== null);
      confirm.disabled =
        !actionable || selected === null || Boolean(context.isBusy?.());
      group.querySelectorAll("button").forEach((button) => {
        button.setAttribute(
          "aria-pressed",
          String(Number(button.textContent) === selected),
        );
        button.disabled = !actionable || Boolean(context.isBusy?.());
      });
    };
    for (let i = 0; i <= 10; i++) {
      const option = node("button", "", String(i));
      option.type = "button";
      option.setAttribute("aria-label", `${i} 分`);
      option.dataset.actionable = String(actionable);
      option.addEventListener("click", () => {
        if (!actionable || context.isBusy?.()) return;
        selected = i;
        update();
        context.onQuestionDraft?.({ degree: i });
      });
      group.append(option);
    }
    confirm.addEventListener("click", () => {
      if (actionable && selected !== null && !context.isBusy?.())
        context.onConfirmAnswer(`${selected}分`);
    });
    update();
    toolbar.append(display, confirm);
    const ends = node("div", "scale-ends");
    ends.append(
      node("span", "", "0 · 完全不影响"),
      node("span", "", "10 · 难以忍受"),
    );
    scale.append(toolbar, group, ends);
    card.append(scale);
  }
  if (data.choices.length) {
    const options = node("div", `visual-choices choices-${data.question_id}`);
    data.choices.forEach((choice) => {
      const option = node("button", `visual-choice choice-${choice.value}`);
      option.type = "button";
      option.setAttribute("aria-label", choice.label);
      option.append(
        visualIcon(choiceIcons[choice.value] || "chat"),
        node("strong", "", choice.label),
        node("span", "choice-arrow", "↗"),
      );
      option.addEventListener("click", () => {
        if (!context.isBusy?.()) context.onChoose?.(choice.label, choice.value);
      });
      options.append(option);
    });
    card.append(options);
  }
  if (spec) {
    const details = node("details", "question-original");
    details.append(
      node("summary", "", "查看完整问题"),
      node("p", "", data.prompt),
    );
    card.append(details);
  }
  return card;
}

export function renderBodyMap(data, context) {
  const map = node("div", "body-map");
  const proposal = data.result_proposal;
  let useProposal = Boolean(
    proposal && context.taskDraft?.proposalId !== proposal.choice,
  );
  const quote = proposal
    ? node("p", "body-proposal", `请核对部位 · “${proposal.evidence}”`)
    : null;
  map.setAttribute("role", "group");
  map.setAttribute("aria-label", "选择主观不适部位，不需要按压身体");
  const canvas = node("div", "body-map-canvas");
  const figure = document.createElementNS(ns, "svg");
  figure.setAttribute("viewBox", "14 0 200 348");
  figure.setAttribute("role", "group");
  figure.setAttribute("aria-label", "人体部位示意图");
  // Repository-owned anatomy only. Broad regions never imply a measured point.
  figure.innerHTML =
    '<g class="body-anatomy"><ellipse cx="114" cy="45" rx="22" ry="28"/><path class="body-silhouette" d="M103 72v12l-26 9q-13 4-17 23L41 195q-2 8 5 11l5 2q7 1 10-7l20-63-1 66-7 114q-1 9 8 9h10q7 0 8-8l15-93 15 93q1 8 8 8h10q9 0 8-9l-7-114-1-66 20 63q3 8 10 7l5-2q7-3 5-11l-19-79q-4-19-17-23l-26-9V72"/></g><g class="body-landmarks body-front"><path d="M106 43h1m14 0h1m-11 14q3 2 6 0M96 96q18 8 36 0M94 151q20 5 40 0"/><circle cx="114" cy="183" r="1.5"/></g><g class="body-landmarks body-back" hidden><path d="M103 32q11-5 22 0M97 108q-9 12 0 23m34-23q9 12 0 23M114 100v99M96 203q18-8 36 0"/></g><g class="body-landmarks body-side-labels" font-size="13" fill="currentColor" stroke="none"><text x="24" y="112" class="body-right-label">右</text><text x="192" y="112" class="body-left-label">左</text></g>';
  const marker = document.createElementNS(ns, "g");
  marker.setAttribute("class", "body-selection-marker");
  marker.setAttribute("aria-hidden", "true");
  figure
    .querySelectorAll(":scope > g")
    .forEach((group) => group.setAttribute("aria-hidden", "true"));
  figure.append(marker);
  const orientations = node("div", "body-map-orientations", "正面示意");
  canvas.append(figure, orientations);
  const controls = node("div", "body-map-controls");
  const scope = node(
    "p",
    "body-map-scope",
    "虚线范围仅表示所选大部位；左右以患者自己为准。",
  );
  const choices = node("div", "body-map-options");
  let expanded = !useProposal;
  const edit = proposal
    ? node("button", "body-proposal-edit", "修改部位")
    : null;
  if (edit) {
    edit.type = "button";
    edit.addEventListener("click", () => {
      if (!actionable || context.isBusy?.()) return;
      expanded = true;
      update();
    });
  }
  const regions = {
    head: [[114, 45, 25, 31]],
    chest: [[114, 123, 29, 26]],
    abdomen: [[114, 179, 27, 26]],
    back: [[114, 155, 28, 52]],
    limbs: [
      [60, 162, 13, 43, 15],
      [168, 162, 13, 43, -15],
      [91, 272, 13, 46, 6],
      [137, 272, 13, 46, -6],
    ],
  };
  const result = node("p", "map-selection sr-only", "点击部位，再确认记录");
  result.setAttribute("aria-live", "polite");
  const confirm = node("button", "map-confirm", "先选择部位");
  confirm.type = "button";
  confirm.disabled = true;
  const options = (data.options || []).filter(
    (choice) =>
      Object.hasOwn(regions, choice.value) ||
      (context.includeOtherRegion && choice.value === "other"),
  );
  const actionable =
    !context.readOnly && typeof context.onChoose === "function";
  const targets = [];
  let selected =
    [
      ...options,
      ...(data.options || []).filter((choice) => choice.value === "other"),
    ].find(
      (choice) =>
        choice.value ===
        (useProposal ? proposal.option : context.taskDraft?.location),
    ) || null;
  function update() {
    choices.hidden = !expanded;
    if (edit) {
      edit.hidden = expanded;
      edit.dataset.ready = String(actionable);
      edit.disabled = !actionable || Boolean(context.isBusy?.());
    }
    choices.querySelectorAll("button").forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.location === selected?.value),
      );
      button.disabled = !actionable || Boolean(context.isBusy?.());
    });
    marker.style.display =
      selected && Object.hasOwn(regions, selected.value) ? "" : "none";
    marker.dataset.location = selected?.value || "";
    orientations.textContent =
      selected?.value === "back"
        ? "背面示意"
        : selected?.value === "limbs"
          ? "手臂／腿示意"
          : "正面示意";
    const back = selected?.value === "back";
    targets.forEach(({ target, value }) => {
      const hidden = back
        ? ["chest", "abdomen"].includes(value)
        : value === "back";
      target.toggleAttribute("hidden", hidden);
      target.setAttribute("tabindex", !hidden && actionable ? "0" : "-1");
      target.setAttribute("aria-pressed", String(selected?.value === value));
      target.setAttribute(
        "aria-disabled",
        String(!actionable || Boolean(context.isBusy?.())),
      );
    });
    figure.querySelector(".body-front").toggleAttribute("hidden", back);
    figure.querySelector(".body-back").toggleAttribute("hidden", !back);
    figure.querySelector(".body-right-label").textContent = back ? "左" : "右";
    figure.querySelector(".body-left-label").textContent = back ? "右" : "左";
    marker.replaceChildren();
    if (selected && Object.hasOwn(regions, selected.value)) {
      regions[selected.value].forEach(([x, y, rx, ry, rotation = 0]) => {
        const area = document.createElementNS(ns, "ellipse");
        for (const [key, value] of Object.entries({
          class: "body-region",
          cx: x,
          cy: y,
          rx,
          ry,
          transform: `rotate(${rotation} ${x} ${y})`,
        }))
          area.setAttribute(key, String(value));
        marker.append(area);
      });
    }
    result.textContent = selected
      ? `已选择：${selected.label}（主观位置）`
      : "点击部位，再确认记录";
    confirm.textContent = selected
      ? context.regionConfirmLabel ||
        `${useProposal ? "核对并记录" : "记录"}：${selected.label}`
      : "先选择部位";
    confirm.dataset.ready = String(actionable && Boolean(selected));
    confirm.disabled = !actionable || !selected || Boolean(context.isBusy?.());
  }
  const select = (choice) => {
    if (!actionable || context.isBusy?.()) return;
    selected = choice;
    useProposal = false;
    context.onTaskDraft?.({
      location: choice.value,
      ...(proposal ? { proposalId: proposal.choice } : {}),
    });
    update();
    const pane = map.closest(".active-task");
    if (pane && map.getBoundingClientRect().height <= pane.clientHeight)
      map.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  options.forEach((choice) => {
    const pin = node("button", `body-pin pin-${choice.value}`, choice.label);
    pin.type = "button";
    pin.dataset.location = choice.value;
    pin.dataset.actionable = String(actionable);
    pin.addEventListener("click", () => select(choice));
    choices.append(pin);
    if (Object.hasOwn(regions, choice.value)) {
      const target = document.createElementNS(ns, "g");
      target.setAttribute("class", "body-region-target");
      target.dataset.actionable = String(actionable);
      target.setAttribute("role", "button");
      target.setAttribute("aria-label", `在图上选择${choice.label}`);
      regions[choice.value].forEach(([cx, cy, rx, ry, rotation = 0]) => {
        const shape = document.createElementNS(ns, "ellipse");
        for (const [key, value] of Object.entries({
          cx,
          cy,
          rx,
          ry,
          transform: `rotate(${rotation} ${cx} ${cy})`,
        }))
          shape.setAttribute(key, String(value));
        target.append(shape);
      });
      target.addEventListener("click", () => select(choice));
      target.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select(choice);
        }
      });
      targets.push({ target, value: choice.value });
      figure.insertBefore(target, marker);
    }
  });
  update();
  confirm.addEventListener("click", () => {
    if (actionable && selected && !context.isBusy?.())
      context.onChoose(
        selected.label,
        useProposal ? proposal.choice : selected.value,
      );
  });
  if (quote) controls.append(quote, edit);
  controls.append(choices, confirm, scope);
  map.append(canvas, controls, result);
  return map;
}
