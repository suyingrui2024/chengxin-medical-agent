// Host observations only. Diff text is never interpreted as HTML or patient data.
export function createChangeReview(api, getCurrent) {
  const $ = (id) => document.getElementById(id);
  const names = {
    added: "新增",
    modified: "修改",
    deleted: "删除",
    unverified: "未核对",
  };
  const signs = { added: "+", modified: "~", deleted: "−", unverified: "?" };
  let generation = 0,
    request = 0,
    selection = 0,
    lastKey = "",
    next = null;
  const entries = new Map();
  const dialog = $("execution-change-dialog");
  const node = (tag, cls, text) => {
    const value = document.createElement(tag);
    if (cls) value.className = cls;
    if (text !== undefined) value.textContent = text;
    return value;
  };
  function counts(target, value) {
    target.replaceChildren();
    for (const [state, label] of Object.entries(names)) {
      if (!value?.[state]) continue;
      target.append(
        node(
          "span",
          `change-count change-${state}`,
          `${signs[state]} ${value[state]} ${label}`,
        ),
      );
    }
    if (value && !Object.values(value).some(Boolean))
      target.append(node("span", "change-empty-count", "未发现文件变化"));
  }
  function clearDetail() {
    selection++;
    $("execution-change-path").textContent = "选择文件查看差异";
    $("execution-change-diff").textContent = "";
    $("execution-change-metadata").textContent = "";
    $("execution-change-hashes").hidden = true;
    $("execution-change-hashes").open = false;
  }
  function renderSummary(value, inDialog = false) {
    counts($("execution-change-badges"), value.counts);
    const incomplete =
      value.counts &&
      (!value.baseline_complete ||
        !value.current_complete ||
        !value.baseline_content_complete ||
        !value.current_content_complete);
    if (incomplete && !Object.values(value.counts).some(Boolean))
      $("execution-change-badges").textContent = "范围未完整核对";
    if (!inDialog) return;
    counts($("execution-change-dialog-counts"), value.counts);
    if (incomplete && !Object.values(value.counts).some(Boolean))
      $("execution-change-dialog-counts").textContent = "范围未完整核对";
    const warning = $("execution-change-warning");
    warning.textContent =
      value.state === "baseline_unavailable"
        ? "此历史任务没有可用的变更基线。原文件仍可在工作区查看。"
        : value.state === "not_started"
          ? "执行任务后开始记录本轮变更。"
          : incomplete ||
              !value.baseline_complete ||
              !value.current_complete ||
              value.counts?.unverified
            ? "部分范围未核对；当前列表不能代表完整检查。"
            : "";
    warning.hidden = !warning.textContent;
    const current = getCurrent();
    const active =
      ["starting", "running", "cancelling"].includes(current?.status) ||
      current?.jobs?.some((j) =>
        ["starting", "running", "stopping"].includes(j.status),
      );
    $("execution-change-scope").textContent = active
      ? "执行中 · 文件可能继续变化"
      : "相对本轮开始 · 读取时观察";
    next = value.next_offset;
    $("execution-change-more").hidden = next === null || next === undefined;
  }
  function currentMatches(id, turn, epoch) {
    const current = getCurrent();
    return (
      epoch === generation &&
      current?.id === id &&
      current?.turns?.at(-1)?.id === turn
    );
  }
  async function selectFile(entry) {
    const current = getCurrent();
    if (!current) return;
    const id = current.id,
      turn = current.turns?.at(-1)?.id,
      epoch = generation,
      ticket = ++selection;
    $("execution-change-path").textContent = entry.path;
    $("execution-change-diff").textContent = "正在读取差异…";
    $("execution-change-metadata").textContent = "";
    for (const button of $("execution-change-files").querySelectorAll("button"))
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.path === entry.path),
      );
    try {
      const value = await api(
        `/api/execution/${id}/changes?path=${encodeURIComponent(entry.path)}`,
      );
      if (
        !currentMatches(id, turn, epoch) ||
        ticket !== selection ||
        !dialog.open ||
        value.turn_id !== turn
      )
        return;
      const latest = value.entries.find((e) => e.path === entry.path) || entry;
      const size = (v) => (v === null ? "不存在" : `${v} B`);
      $("execution-change-metadata").textContent =
        `${names[latest.state] || "文件"} · ${size(latest.before_size)} → ${size(latest.after_size)}`;
      if (
        latest.before_mode != null &&
        latest.after_mode != null &&
        latest.before_mode !== latest.after_mode
      )
        $("execution-change-metadata").textContent +=
          ` · 权限 ${latest.before_mode.toString(8)} → ${latest.after_mode.toString(8)}`;
      $("execution-change-hashes").hidden = false;
      $("execution-change-hash-values").textContent =
        `之前 · ${latest.before_sha256 || "未提供"}\n现在 · ${latest.after_sha256 || "未提供"}`;
      const detail = value.detail;
      const pre = $("execution-change-diff");
      pre.replaceChildren();
      if (!detail?.available) {
        pre.textContent = "暂无文本预览；可对照大小与文件指纹。";
        return;
      }
      if (!detail.diff) {
        pre.textContent =
          "当前可展示文本没有差异。请同时核对文件状态与内容哈希。";
        return;
      }
      for (const line of detail.diff.split("\n")) {
        const cls =
          line.startsWith("+++") ||
          line.startsWith("---") ||
          line.startsWith("@@")
            ? "change-diff-meta"
            : line.startsWith("+")
              ? "change-diff-added"
              : line.startsWith("-")
                ? "change-diff-deleted"
                : "";
        pre.append(node("span", `change-diff-line ${cls}`, line || " "));
      }
      if (detail.truncated)
        pre.append(
          node("span", "change-diff-line change-diff-meta", "… 差异预览已截断"),
        );
    } catch (error) {
      if (
        currentMatches(id, turn, epoch) &&
        ticket === selection &&
        dialog.open
      )
        $("execution-change-diff").textContent = error.message;
    }
  }
  async function refresh(inDialog = false, offset = 0) {
    const current = getCurrent();
    if (!current) return;
    const id = current.id,
      turn = current.turns?.at(-1)?.id,
      epoch = generation,
      ticket = ++request;
    if (inDialog) $("execution-change-refresh").disabled = true;
    try {
      const value = await api(
        `/api/execution/${id}/changes?offset=${offset}&limit=200`,
      );
      if (
        !currentMatches(id, turn, epoch) ||
        ticket !== request ||
        value.turn_id !== (turn || null)
      )
        return;
      renderSummary(value, inDialog);
      if (!inDialog || !dialog.open) return;
      if (!offset) {
        entries.clear();
        clearDetail();
      }
      for (const entry of value.entries) entries.set(entry.path, entry);
      const list = $("execution-change-files");
      list.replaceChildren();
      for (const entry of entries.values()) {
        const button = node("button", "change-file");
        button.type = "button";
        button.dataset.path = entry.path;
        button.setAttribute("aria-pressed", "false");
        button.append(
          node(
            "span",
            `change-state change-${entry.state}`,
            `${signs[entry.state]} ${names[entry.state]}`,
          ),
          node("span", "change-filename", entry.path),
        );
        button.addEventListener("click", () => selectFile(entry));
        list.append(button);
      }
      $("execution-change-pagination").textContent = value.total_changes
        ? `${entries.size} / ${value.total_changes} 项`
        : "";
      if (!entries.size)
        list.append(
          node(
            "p",
            "change-no-files",
            value.counts ? "当前未发现可列出的变化" : "尚无变更记录",
          ),
        );
      if (!offset && entries.size)
        void selectFile(entries.values().next().value);
    } catch (error) {
      if (!currentMatches(id, turn, epoch) || ticket !== request) return;
      $("execution-change-badges").textContent = "暂不可用";
      if (inDialog && dialog.open) {
        $("execution-change-warning").hidden = false;
        $("execution-change-warning").textContent = error.message;
      }
    } finally {
      if (currentMatches(id, turn, epoch) && ticket === request)
        $("execution-change-refresh").disabled = false;
    }
  }
  $("execution-review-changes").addEventListener("click", () => {
    if (!getCurrent()) return;
    dialog.showModal();
    void refresh(true);
  });
  $("execution-change-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    selection++;
  });
  $("execution-change-refresh").addEventListener("click", () => refresh(true));
  $("execution-change-more").addEventListener("click", () =>
    refresh(true, next || 0),
  );
  return {
    update(snapshot) {
      $("execution-review-changes").disabled =
        !snapshot.turns?.length || Boolean(snapshot.storage_error);
      const key = `${snapshot.id}:${snapshot.turns?.at(-1)?.id}:${snapshot.status}:${JSON.stringify(snapshot.jobs)}`;
      if (key === lastKey) return;
      lastKey = key;
      if (
        !dialog.open &&
        snapshot.turns?.length &&
        !["starting", "running", "cancelling"].includes(snapshot.status)
      )
        void refresh();
    },
    reset() {
      generation++;
      request++;
      selection++;
      lastKey = "";
      next = null;
      entries.clear();
      if (dialog.open) dialog.close();
      $("execution-review-changes").disabled = true;
      $("execution-change-badges").textContent = "执行后核对";
      $("execution-change-files").replaceChildren();
      $("execution-change-refresh").disabled = false;
      clearDetail();
    },
  };
}
