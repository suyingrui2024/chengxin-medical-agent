// Render only the host ledger, never an assistant's purported question/answer JSON.
export function createClarificationUI(api, getCurrent, applySnapshot) {
  const container = document.getElementById("execution-clarifications");
  const cards = new Map();
  let context = "";
  const node = (tag, cls, text) => {
    const value = document.createElement(tag);
    if (cls) value.className = cls;
    if (text !== undefined) value.textContent = text;
    return value;
  };
  function update(snapshot) {
    const key = `${snapshot.id}:${snapshot.turn_id}`;
    if (context !== key) {
      context = key;
      cards.clear();
      container.replaceChildren();
    }
    const questions = (snapshot.clarifications || []).filter(
      (q) => q.turn_id === snapshot.turn_id,
    );
    container.hidden = !questions.length;
    for (const value of questions) {
      let card = cards.get(value.id);
      const signature = JSON.stringify([
        value.status,
        value.answer,
        value.reason,
      ]);
      if (card?.signature === signature) continue;
      const previous = card;
      const element = node("section", "execution-question");
      element.dataset.questionId = value.id;
      card = { element, signature, busy: false };
      cards.set(value.id, card);
      if (previous) previous.element.replaceWith(element);
      else container.append(element);
      const pending = value.status === "pending";
      if (!pending) {
        element.classList.add("execution-question-history");
        const details = node("details");
        const label =
          value.status === "answered"
            ? "已核对"
            : value.status === "submitted"
              ? "回答已发送 · 等待接续"
              : value.status === "interrupted"
                ? "上次问题已中断"
                : "问题已停止";
        details.append(node("summary", "", `${label}：${value.question}`));
        if (value.answer)
          details.append(node("p", "execution-answer-text", value.answer));
        if (["closed", "interrupted"].includes(value.status))
          details.append(
            node("small", "", "旧问题不可继续提交；可在后续任务中说明需求。"),
          );
        element.append(details);
        continue;
      }
      const header = node("div", "execution-question-heading");
      header.append(
        node("span", "execution-question-mark", "?"),
        node("strong", "", "需要你核对"),
        node(
          "small",
          "",
          value.session_id === snapshot.id ? "主任务" : "子任务",
        ),
      );
      element.append(header, node("h3", "", value.question));
      if (value.context) {
        const details = node("details", "execution-question-context");
        details.append(
          node("summary", "", "为什么需要核对"),
          node("p", "", value.context),
        );
        element.append(details);
      }
      const choices = node("div", "execution-question-choices");
      const error = node("p", "execution-question-error");
      error.setAttribute("role", "status");
      error.hidden = true;
      const controls = [];
      const submit = async (answer) => {
        if (card.busy || !answer.trim() || context !== key) return;
        card.busy = true;
        controls.forEach((control) => (control.disabled = true));
        error.hidden = true;
        try {
          const result = await api(`/api/execution/${snapshot.id}/answer`, {
            method: "POST",
            body: { request_id: value.id, turn_id: value.turn_id, answer },
          });
          const current = getCurrent();
          if (
            context === key &&
            current?.id === snapshot.id &&
            current?.turn_id === value.turn_id &&
            result.sequence >= current.sequence
          )
            applySnapshot(result);
        } catch (issue) {
          if (context !== key || cards.get(value.id) !== card) return;
          error.textContent = issue.message;
          error.hidden = false;
          controls.forEach((control) => (control.disabled = false));
        } finally {
          card.busy = false;
        }
      };
      for (const option of value.options) {
        const button = node("button", "execution-question-choice", option);
        button.type = "button";
        button.addEventListener("click", () => void submit(option));
        choices.append(button);
        controls.push(button);
      }
      element.append(choices);
      const form = node("form", "execution-question-form");
      const input = node("textarea");
      input.rows = 2;
      input.maxLength = 2000;
      input.placeholder = "写下你的回答…";
      input.setAttribute("aria-label", "补充回答");
      const send = node("button", "secondary-button", "发送回答 ↗");
      send.type = "submit";
      form.append(input, send);
      controls.push(input, send);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (input.value.trim()) void submit(input.value.trim());
      });
      if (value.options.length) {
        form.hidden = true;
        const custom = node(
          "button",
          "text-button execution-question-custom",
          "自行说明",
        );
        custom.type = "button";
        custom.addEventListener("click", () => {
          form.hidden = false;
          custom.hidden = true;
          input.focus();
        });
        element.append(custom);
        controls.push(custom);
      }
      element.append(
        form,
        error,
        node(
          "small",
          "execution-question-limit",
          "等待期间仍计入本轮时限，其他独立子任务可能继续。",
        ),
      );
    }
  }
  return {
    update,
    reset() {
      context = "";
      cards.clear();
      container.replaceChildren();
      container.hidden = true;
    },
  };
}
