// DOM construction shared by views; callers retain their own text contracts.
export const $ = (selector) => document.querySelector(selector);

export function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

export function textElement(tag, className, text) {
  return node(tag, className, text == null ? undefined : String(text));
}

export function action(label, className, callback) {
  const element = textElement("button", className, label);
  element.type = "button";
  element.addEventListener("click", callback);
  return element;
}

export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function emptyState(message) {
  const element = textElement("div", "empty-state compact");
  element.append(icon("cards"), textElement("p", "", message));
  return element;
}
