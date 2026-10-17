// A compact view of host state. No inferred plan, percentage or model-authored status.
const paths = {
  ready: "M8 4h8l4 4v12H4V4h4m4 4v8m-4-4h8",
  running: "m9 5 10 7-10 7Z",
  waiting:
    "M8 10V5a2 2 0 0 1 4 0v7-5-3a2 2 0 0 0-2 3l5 8h7l3-8V8a2 2 0 0 0-4 0v3",
  completed: "m5 12 4 4L19 6",
  attention: "M12 4 2 21h20ZM12 10v5m0 3v.1",
  stopped: "M7 5v14m10-14v14",
  image: "M3 3h18v18H3Zm0 13 6-6 5 5 3-3 4 4M16 7h.01",
  table: "M3 4h18v16H3Zm0 5h18M3 14h18M10 4v16",
  file: "M5 3h9l5 5v13H5Zm9 0v6h5M8 13h8m-8 4h6",
};
function icon(kind) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", paths[kind]);
  svg.append(path);
  return svg;
}
const fileKind = (path) =>
  /\.(png|jpe?g)$/i.test(path)
    ? "image"
    : /\.(csv|tsv)$/i.test(path)
      ? "table"
      : "file";

export function createExecutionFocus(getCurrent, openFile) {
  const $ = (id) => document.getElementById(id);
  const pane = $("execution-focus");
  const shortcuts = $("execution-quick-files");
  const browser = $("execution-file-browser");
  const quick = $("execution-quick-look");
  let signature = "",
    context = "";
  quick.addEventListener("toggle", () => {
    if (quick.open) browser.open = false;
  });
  browser.addEventListener("toggle", () => {
    if (browser.open) quick.open = false;
  });
  function reset() {
    pane.hidden = true;
    $("execution-quick-look").hidden = true;
    shortcuts.replaceChildren();
    signature = "";
    context = "";
  }
  function update(snapshot, needsReview = false) {
    if (!snapshot) return reset();
    if (context !== snapshot.id) {
      context = snapshot.id;
      quick.open = false;
      browser.open = true;
    }
    const questions = (snapshot.clarifications || []).filter(
      (q) => q.turn_id === snapshot.turn_id,
    );
    const pending = questions.some((q) => q.status === "pending");
    const submitted = questions.some((q) => q.status === "submitted");
    const activeJobs = (snapshot.jobs || []).filter((job) =>
      ["starting", "running", "stopping"].includes(job.status),
    ).length;
    let kind = "ready",
      label = "准备开始";
    if (snapshot.storage_error) {
      kind = "attention";
      label = "工作目录不可用";
    } else if (snapshot.status === "cancelling") {
      kind = "stopped";
      label = "正在停止任务";
    } else if (pending || submitted) {
      kind = "waiting";
      label = pending ? "等待你的回答" : "回答已发送，等待接续";
    } else if (activeJobs) {
      kind = "running";
      label = `${activeJobs} 个后台任务运行中`;
    } else if (["starting", "running"].includes(snapshot.status)) {
      kind = "running";
      label =
        snapshot.status === "starting" ? "正在准备工作区" : "正在处理任务";
    } else if (needsReview || snapshot.status === "failed") {
      kind = "attention";
      label = "有未完成项，请核对";
    } else if (["cancelled", "interrupted"].includes(snapshot.status)) {
      kind = "stopped";
      label = snapshot.status === "cancelled" ? "任务已停止" : "上次任务已中断";
    } else if (snapshot.restored || snapshot.execution_configured === false) {
      label = "已保存的工作区";
    } else if (snapshot.status === "completed") {
      kind = "completed";
      label = "本轮已完成";
    }
    pane.hidden = false;
    if (pane.dataset.state !== kind) {
      pane.dataset.state = kind;
      $("execution-focus-symbol").replaceChildren(icon(kind));
    }
    $("execution-focus-label").textContent = label;
    const title = snapshot.title || "描述目标，逐步完成";
    $("execution-focus-task").textContent = title;
    $("execution-focus-task").title = title;
    const respond = $("execution-focus-respond");
    respond.hidden =
      !pending ||
      Boolean(snapshot.storage_error) ||
      snapshot.status === "cancelling";
    respond.onclick = () => {
      const target = document.querySelector(
        ".execution-question:not(.execution-question-history) input, .execution-question:not(.execution-question-history) textarea, .execution-question:not(.execution-question-history) button",
      );
      target?.scrollIntoView({ block: "nearest" });
      target?.focus({ preventScroll: true });
    };
    const files = snapshot.storage_error
      ? []
      : (snapshot.artifacts || [])
          .filter((file) =>
            /\.(png|jpe?g|csv|tsv|txt|md|py|js|mjs|json|html|css|svg|xml|log|ya?ml|sh|ps1|r)$/i.test(
              file.path,
            ),
          )
          .sort((a, b) => {
            const rank = { image: 0, table: 1, file: 2 };
            return (
              rank[fileKind(a.path)] - rank[fileKind(b.path)] ||
              a.path.localeCompare(b.path)
            );
          })
          .slice(0, 3);
    $("execution-quick-look").hidden = !files.length;
    $("execution-quick-note").textContent = "当前目录 · 最多 3 项";
    const next = JSON.stringify([snapshot.id, files.map((file) => file.path)]);
    if (signature === next) return;
    signature = next;
    const focused = shortcuts.contains(document.activeElement)
      ? document.activeElement.dataset.path
      : null;
    shortcuts.replaceChildren();
    for (const file of files) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.path = file.path;
      button.dataset.previewFile = "true";
      button.setAttribute("aria-label", `快速查看：${file.path}`);
      button.setAttribute("aria-pressed", "false");
      button.title = file.path;
      const name = document.createElement("span");
      name.textContent = file.path.split("/").at(-1);
      button.append(icon(fileKind(file.path)), name);
      const id = snapshot.id;
      button.addEventListener("click", async () => {
        if (getCurrent()?.id !== id || getCurrent()?.storage_error) return;
        await openFile(file);
        if (getCurrent()?.id !== id || document.activeElement !== button)
          return;
        $("execution-preview").scrollIntoView({ block: "nearest" });
        $("execution-preview").focus({ preventScroll: true });
      });
      shortcuts.append(button);
      if (focused === file.path) button.focus({ preventScroll: true });
    }
  }
  return { update, reset };
}
