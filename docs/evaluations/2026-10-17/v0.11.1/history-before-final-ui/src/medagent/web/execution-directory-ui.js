// Browse one actual directory at a time. Counts describe that listing, never the whole project.
export function createDirectoryUI(api, getCurrent, onSelect, onListed) {
  const $ = (id) => document.getElementById(id);
  let generation = 0,
    pending = false,
    foreground = false,
    lastLoad = 0,
    data = null,
    pageIndex = 0,
    cursors = [null],
    signature = "",
    suspended = false;
  function node(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function status(text) {
    if ($("execution-directory-status").textContent !== text)
      $("execution-directory-status").textContent = text;
  }
  function controls() {
    $("execution-directory-pages").hidden =
      pageIndex === 0 && !data?.next_cursor;
    const disabled = !getCurrent() || Boolean(getCurrent()?.storage_error);
    $("execution-directory-previous").disabled =
      disabled || (pending && foreground) || pageIndex === 0;
    $("execution-directory-next").disabled =
      disabled || (pending && foreground) || !data?.next_cursor;
    $("execution-directory-hidden").disabled = disabled;
    $("execution-file-path").disabled = disabled;
    $("execution-file-open-button").disabled = disabled;
  }
  function reset() {
    generation++;
    pending = false;
    foreground = false;
    lastLoad = 0;
    data = null;
    pageIndex = 0;
    cursors = [null];
    signature = "";
    suspended = false;
    $("execution-files").replaceChildren();
    $("execution-directory-breadcrumb").replaceChildren();
    $("execution-directory-hidden").checked = false;
    $("execution-file-path").value = "";
    $("execution-file-count").textContent = "—";
    $("execution-directory-page").textContent = "";
    status("选择工作区后按目录查看文件。");
    controls();
  }
  function render(value, index, focus = false, note = "") {
    const nextSignature = JSON.stringify([value.path, value.entries]);
    if (nextSignature !== signature) {
      const focused = document.activeElement?.closest("#execution-files button")
        ?.dataset.path;
      $("execution-files").replaceChildren();
      for (const entry of value.entries) {
        const row = node("button", "execution-file");
        row.type = "button";
        row.dataset.path = entry.path;
        const directory = entry.kind === "directory",
          file = entry.kind === "file";
        if (file) row.dataset.previewFile = "true";
        const format = directory
          ? "夹"
          : file
            ? /\.(png|jpe?g)$/i.test(entry.name)
              ? "图"
              : /\.(csv|tsv)$/i.test(entry.name)
                ? "表"
                : "文"
            : entry.kind === "link"
              ? "链"
              : "?";
        const badge = node("span", "execution-file-kind", format);
        badge.setAttribute("aria-hidden", "true");
        row.append(
          badge,
          node("span", "", entry.name),
          node(
            "small",
            "",
            directory
              ? "进入 ↗"
              : file
                ? `${entry.size} B`
                : entry.kind === "link"
                  ? "不展开"
                  : "不可预览",
          ),
        );
        if (directory) {
          row.setAttribute("aria-label", `进入目录 ${entry.path}`);
          row.addEventListener(
            "click",
            () => void load(entry.path, null, 0, [null], true),
          );
        } else if (file)
          row.addEventListener("click", () => void onSelect(entry));
        else row.disabled = true;
        $("execution-files").append(row);
      }
      if (!value.entries.length)
        $("execution-files").append(
          node(
            "p",
            "",
            value.scan_complete
              ? "此目录没有可显示条目。"
              : "当前扫描未返回条目，列表尚不完整。",
          ),
        );
      if (focused && !focus)
        [...$("execution-files").querySelectorAll("button")]
          .find((n) => n.dataset.path === focused)
          ?.focus({ preventScroll: true });
      if (!data || data.path !== value.path) {
        const nav = $("execution-directory-breadcrumb");
        nav.replaceChildren();
        const parts = value.path ? value.path.split("/") : [];
        for (let i = 0; i <= parts.length; i++) {
          const target = parts.slice(0, i).join("/");
          const b = node("button", "", i === 0 ? "工作区" : parts[i - 1]);
          b.type = "button";
          b.title = target || "工作区根目录";
          b.setAttribute("aria-label", `浏览目录 ${target || "工作区根目录"}`);
          if (i === parts.length) b.setAttribute("aria-current", "location");
          b.addEventListener(
            "click",
            () => void load(target, null, 0, [null], true),
          );
          nav.append(b);
        }
        nav.scrollLeft = nav.scrollWidth;
      }
      signature = nextSignature;
    }
    $("execution-file-count").textContent =
      value.total_entries === null ? "—" : String(value.total_entries);
    $("execution-directory-page").textContent = `第 ${index + 1} 页`;
    const reasons = {
      entry_limit: "达到目录扫描上限",
      time_limit: "达到读取时限",
      unsupported_name: "部分名称不是UTF-8",
      unreadable_entry: "部分条目不可读取",
    };
    status(
      [
        value.scan_complete
          ? `本目录${value.include_hidden ? "" : "可见"} ${value.total_entries} 项 · 本页 ${value.entries.length} 项`
          : `目录扫描不完整 · 本页 ${value.entries.length} 项`,
        ...value.reasons.map((r) => reasons[r] || "部分内容未知"),
        note,
      ]
        .filter(Boolean)
        .join(" · "),
    );
    if (focus) {
      $("execution-files").scrollTop = 0;
      $("execution-files").focus({ preventScroll: true });
    }
  }
  async function load(
    path = "",
    cursor = null,
    index = 0,
    stack = [null],
    focus = false,
    note = "",
    background = false,
  ) {
    const current = getCurrent();
    if (!current || current.storage_error) return;
    const request = ++generation,
      id = current.id;
    pending = true;
    foreground = !background;
    lastLoad = Date.now();
    controls();
    try {
      const value = await api(
        `/api/execution/${id}/directory?path=${encodeURIComponent(path)}&hidden=${$("execution-directory-hidden").checked ? 1 : 0}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      );
      if (
        request !== generation ||
        getCurrent()?.id !== id ||
        getCurrent()?.storage_error
      )
        return;
      if (
        value?.schema_version !== "execution-directory/v1" ||
        value.path !== path ||
        !Array.isArray(value.entries) ||
        value.entries.length > 200 ||
        typeof value.scan_complete !== "boolean" ||
        !Array.isArray(value.reasons) ||
        !(
          value.total_entries === null || Number.isInteger(value.total_entries)
        ) ||
        !(
          value.next_cursor === null || typeof value.next_cursor === "string"
        ) ||
        value.entries.some(
          (e) =>
            typeof e.path !== "string" ||
            typeof e.name !== "string" ||
            !["directory", "file", "link", "other", "unavailable"].includes(
              e.kind,
            ),
        )
      )
        throw new Error("目录响应无效，请重新读取。");
      render(value, index, focus, note);
      data = value;
      pageIndex = index;
      cursors = stack;
      onListed(value.entries.filter((e) => e.kind === "file"));
    } catch (error) {
      if (request !== generation || getCurrent()?.id !== id) return;
      if (error.code === "directory_changed" && cursor) {
        void load(
          path,
          null,
          0,
          [null],
          false,
          "目录条目变化，已返回第一页",
          background,
        );
        return;
      }
      status(`目录未能读取：${error.message}`);
    } finally {
      if (request === generation) {
        pending = false;
        controls();
      }
    }
  }
  function update(snapshot) {
    if (snapshot.storage_error) {
      if (!suspended) {
        generation++;
        pending = false;
        suspended = true;
        $("execution-files").replaceChildren();
        $("execution-file-count").textContent = "—";
        status("工作目录不可读取。");
      }
      controls();
      return;
    }
    if (suspended) {
      suspended = false;
      signature = "";
      lastLoad = 0;
    }
    if (!pending && Date.now() - lastLoad >= 2000)
      void load(
        data?.path || "",
        cursors[pageIndex],
        pageIndex,
        cursors,
        false,
        "",
        true,
      );
  }
  $("execution-directory-next").addEventListener("click", () => {
    if (!(pending && foreground) && data?.next_cursor)
      void load(
        data.path,
        data.next_cursor,
        pageIndex + 1,
        [...cursors.slice(0, pageIndex + 1), data.next_cursor],
        true,
      );
  });
  $("execution-directory-previous").addEventListener("click", () => {
    if (!(pending && foreground) && pageIndex > 0)
      void load(
        data.path,
        cursors[pageIndex - 1],
        pageIndex - 1,
        cursors,
        true,
      );
  });
  $("execution-directory-hidden").addEventListener(
    "change",
    () => void load(data?.path || "", null, 0, [null]),
  );
  $("execution-file-open").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!getCurrent() || getCurrent().storage_error) return;
    const path = $("execution-file-path").value;
    if (!path) return;
    void onSelect({ path, revision: null });
    const parent = path.includes("/")
      ? path.slice(0, path.lastIndexOf("/"))
      : "";
    void load(parent, null, 0, [null]);
  });
  return {
    reset,
    update,
    files: () =>
      suspended ? [] : data?.entries.filter((e) => e.kind === "file") || [],
  };
}
