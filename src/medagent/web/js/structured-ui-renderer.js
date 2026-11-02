// Renderer core: registry + dispatch, wired from the family installers.
import { el, text, list, UI_VERSION } from "./structured-ui-primitives.js";
import { installCards } from "./structured-ui-cards.js";
import { installForms } from "./structured-ui-forms.js";
import { installTask } from "./structured-ui-task.js";
import { installClosing } from "./structured-ui-closing.js";

export function createRenderer({ taskProgress = new Map() } = {}) {
  const registry = new Map();
  function register(type, validate, render) {
    if (registry.has(type)) throw new Error("Component already registered");
    registry.set(type, { validate, render });
  }
  function fallback() {
    const node = el("article", "structured-fallback");
    node.setAttribute("role", "status");
    node.append(
      el("strong", "", "这部分内容暂时无法显示"),
      el(
        "p",
        "",
        "展示格式不兼容或内容不完整。已有记录仍保留，可回看记录或重新加载。",
      ),
    );
    return node;
  }
  function blocks(envelope) {
    if (envelope?.schema_version !== UI_VERSION)
      return [{ id: "unsupported-version", type: "unsupported" }];
    try {
      return list(envelope.blocks);
    } catch {
      return [{ id: "invalid-envelope", type: "unsupported" }];
    }
  }
  function render(block, context = {}) {
    try {
      text(block.id);
      const component = registry.get(block.type);
      if (!component || !block.data || typeof block.data !== "object")
        return fallback();
      component.validate(block.data);
      const node = component.render(block.data, context);
      node.dataset.blockId = block.id;
      node.dataset.blockType = block.type;
      return node;
    } catch {
      return fallback();
    }
  }
  function renderEnvelope(envelope, context = {}) {
    const fragment = document.createDocumentFragment();
    for (const block of blocks(envelope))
      fragment.append(render(block, context));
    return fragment;
  }

  installCards(register);
  const forms = installForms(register);
  installTask(register, taskProgress);
  installClosing(register, forms.renderScaleResult);

  return { register, render, renderEnvelope, blocks };
}
