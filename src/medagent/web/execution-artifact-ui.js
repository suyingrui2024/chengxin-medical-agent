// Workspace files are inert snapshots. Neither file text nor model HTML is executed.
import { numericColumns, renderArtifactChart } from "./artifact-chart-ui.js";
export function createArtifactPreview(api, getCurrent) {
  const $ = (id) => document.getElementById(id);
  let ticket = 0,
    selected = null,
    content = null,
    unavailable = false,
    source = false,
    chart = false,
    chartColumn = null,
    checking = 0,
    lastChecked = 0;
  const empty = "选择文件查看图像、表格或代码。";
  function setMeta(text) {
    const region = $("execution-preview-meta");
    if (region.textContent !== text) region.textContent = text;
  }
  function element(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function close() {
    if ($("execution-artifact-dialog").open)
      $("execution-artifact-dialog").close();
    $("execution-artifact-large").replaceChildren();
  }
  function markSelection() {
    for (const button of document.querySelectorAll(
      "#execution-files button[data-preview-file], #execution-quick-files button[data-preview-file]",
    )) {
      const active = button.dataset.path === selected?.path;
      button.classList.toggle("is-selected", active);
      if (button.getAttribute("aria-pressed") !== String(active))
        button.setAttribute("aria-pressed", String(active));
    }
  }
  function reset(message = empty, title = "文件预览") {
    ticket++;
    checking = 0;
    selected = content = null;
    source = false;
    chart = false;
    chartColumn = null;
    unavailable = title === "文件暂不可用";
    close();
    $("execution-preview-title").textContent = title;
    $("execution-preview").className = "artifact-preview-empty";
    $("execution-preview").textContent = message;
    setMeta("");
    $("execution-preview-controls").hidden = true;
    markSelection();
  }
  function renderInto(target, value, request) {
    target.replaceChildren();
    target.className = `artifact-preview-${value.kind}`;
    if (value.kind === "image") {
      const image = element("img", "artifact-image");
      image.alt = `研究产物：${value.path}`;
      image.src = `/api/execution/${encodeURIComponent(selected.id)}/image?path=${encodeURIComponent(value.path)}&sha256=${value.sha256}`;
      image.addEventListener("error", () => {
        if (request !== ticket || !image.isConnected) return;
        image.replaceWith(
          element(
            "p",
            "artifact-error",
            "图像未能解码，可能已变化或损坏。请重新读取。",
          ),
        );
      });
      target.append(image);
    } else if (value.kind === "table" && chart && !source) {
      target.className = "artifact-preview-chart";
      const figure = renderArtifactChart(value, chartColumn, (index) => {
        if (request !== ticket || content !== value || !target.isConnected)
          return;
        chartColumn = index;
        renderInto($("execution-preview"), value, request);
        if ($("execution-artifact-dialog").open)
          renderInto($("execution-artifact-large"), value, request);
        target.querySelector("select")?.focus({ preventScroll: true });
      });
      if (figure) target.append(figure);
    } else if (value.kind === "table" && !source) {
      const table = element("table", "artifact-table");
      table.setAttribute("aria-label", value.path);
      const header = element("thead"),
        body = element("tbody");
      const heading = element("tr");
      heading.append(element("th", "artifact-row-number", "行"));
      for (let i = 0; i < value.columns; i++) {
        const cell = element("th", "", value.rows[0]?.[i] ?? "");
        cell.scope = "col";
        heading.append(cell);
      }
      header.append(heading);
      value.rows.slice(1).forEach((row, index) => {
        const line = element("tr");
        const number = element("th", "artifact-row-number", String(index + 2));
        number.scope = "row";
        line.append(number);
        for (let i = 0; i < value.columns; i++)
          line.append(element("td", "", row[i] ?? ""));
        body.append(line);
      });
      table.append(header, body);
      target.append(table);
    } else {
      target.append(element("pre", "artifact-text", value.text));
    }
  }
  function valid(value, path) {
    if (
      value?.schema_version !== "execution-artifact/v1" ||
      value.path !== path ||
      !/^[a-f0-9]{64}$/.test(value.sha256) ||
      typeof value.revision !== "string" ||
      !Number.isInteger(value.size) ||
      value.size < 0
    )
      return false;
    if (value.kind === "image")
      return (
        ["image/png", "image/jpeg"].includes(value.mime) &&
        Number.isInteger(value.width) &&
        Number.isInteger(value.height) &&
        value.width > 0 &&
        value.height > 0 &&
        Math.max(value.width, value.height) <= 8192 &&
        value.width * value.height <= 16777216
      );
    if (
      !["text", "table"].includes(value.kind) ||
      typeof value.text !== "string" ||
      value.text.length > 128000
    )
      return false;
    return (
      value.kind !== "table" ||
      (Number.isInteger(value.columns) &&
        value.columns > 0 &&
        value.columns <= 24 &&
        Number.isInteger(value.total_rows) &&
        value.total_rows >= value.rows?.length &&
        Array.isArray(value.rows) &&
        value.rows.length > 0 &&
        value.rows.length <= 100 &&
        value.rows.every(
          (row) =>
            Array.isArray(row) &&
            row.length <= 24 &&
            row.every((cell) => typeof cell === "string"),
        ))
    );
  }
  function metadata(value) {
    const size =
      value.size < 1024
        ? `${value.size} B`
        : `${(value.size / 1024).toFixed(1)} KB`;
    const shape =
      value.kind === "image"
        ? `${value.mime === "image/png" ? "PNG" : "JPEG"} · ${value.width} × ${value.height}`
        : value.kind === "table"
          ? `${value.total_rows} 行 · ${value.columns}${value.columns_truncated ? "+" : ""} 列 · 首行作表头`
          : "UTF-8";
    const notes = [shape, size, "读取快照"];
    if (value.rows_truncated) notes.push("仅显示前100行");
    if (value.columns_truncated) notes.push("仅显示前24列");
    if (value.ragged) notes.push("部分行列数不同");
    if (value.notice) notes.push(value.notice);
    setMeta(notes.join(" · "));
    const title = `SHA256 ${value.sha256}`;
    if ($("execution-preview-meta").title !== title)
      $("execution-preview-meta").title = title;
  }
  async function open(artifact) {
    const current = getCurrent();
    if (!current || current.storage_error) return;
    const request = ++ticket;
    checking = 0;
    lastChecked = Date.now();
    close();
    selected = {
      id: current.id,
      path: artifact.path,
      seenRevision: artifact.revision,
    };
    content = null;
    source = false;
    chart = false;
    chartColumn = null;
    $("execution-preview-title").textContent = artifact.path;
    $("execution-preview").className = "artifact-preview-empty";
    $("execution-preview").textContent = "正在读取文件…";
    setMeta("");
    $("execution-preview-controls").hidden = false;
    $("execution-preview-reload").disabled = true;
    $("execution-preview-expand").disabled = true;
    $("execution-preview-source").hidden = true;
    $("execution-preview-chart").hidden = true;
    $("execution-preview-chart").textContent = "查看图表";
    $("execution-preview-chart").setAttribute("aria-pressed", "false");
    markSelection();
    const active = () =>
      ticket === request &&
      getCurrent()?.id === selected?.id &&
      !getCurrent()?.storage_error;
    try {
      const value = await api(
        `/api/execution/${current.id}/preview?path=${encodeURIComponent(artifact.path)}`,
      );
      if (!active()) return;
      if (!valid(value, artifact.path))
        throw new Error("文件预览响应无效，请重新读取。");
      content = value;
      renderInto($("execution-preview"), value, request);
      metadata(value);
      $("execution-preview-expand").disabled = false;
      $("execution-preview-source").hidden = value.kind !== "table";
      $("execution-preview-chart").hidden = !numericColumns(value).length;
      $("execution-preview-source").textContent = "查看原文";
      selected.seenRevision = getCurrent().artifacts.find(
        (a) => a.path === selected.path,
      )?.revision;
    } catch (error) {
      if (!active()) return;
      $("execution-preview").textContent = error.message;
      $("execution-preview").className = "artifact-error";
    } finally {
      if (active()) $("execution-preview-reload").disabled = false;
    }
  }
  async function checkSnapshot() {
    if (checking || !selected || !content || Date.now() - lastChecked < 2000)
      return;
    const request = ticket,
      expected = content,
      workspace = selected.id;
    checking = request;
    lastChecked = Date.now();
    const active = () =>
      ticket === request &&
      getCurrent()?.id === workspace &&
      !getCurrent()?.storage_error;
    try {
      const status = await api(
        `/api/execution/${workspace}/artifact-status?path=${encodeURIComponent(expected.path)}&sha256=${expected.sha256}`,
      );
      if (!active()) return;
      if (
        status?.schema_version !== "artifact-freshness/v1" ||
        status.path !== expected.path ||
        typeof status.matches !== "boolean"
      )
        throw new Error("无法核对文件快照");
      if (status.matches) metadata(expected);
      else {
        setMeta("文件已变化 · 重新读取后查看最新内容");
        close();
      }
    } catch {
      if (active()) {
        setMeta("当前文件未能重新核对 · 仍显示读取快照");
        close();
      }
    } finally {
      if (checking === request) checking = 0;
    }
  }
  function update(snapshot) {
    if (snapshot.storage_error) {
      if (!unavailable)
        reset("工作目录不可读取，暂时无法预览文件。", "文件暂不可用");
      return;
    }
    if (unavailable) reset();
    markSelection();
    if (!selected || !content) return;
    const artifact = snapshot.artifacts.find((a) => a.path === selected.path);
    if (
      artifact &&
      artifact.revision !== selected.seenRevision &&
      artifact.revision !== content.revision
    ) {
      setMeta("文件已变化 · 重新读取后查看最新内容");
      close();
    }
    if (artifact) selected.seenRevision = artifact.revision;
    void checkSnapshot();
  }
  $("execution-preview-reload").addEventListener("click", () => {
    if (selected)
      void open({
        path: selected.path,
        revision: getCurrent()?.artifacts.find((a) => a.path === selected.path)
          ?.revision,
      });
  });
  $("execution-preview-expand").addEventListener("click", () => {
    if (!content) return;
    $("execution-artifact-title").textContent = content.path;
    $("execution-artifact-caption").textContent = $(
      "execution-preview-meta",
    ).textContent;
    renderInto($("execution-artifact-large"), content, ticket);
    $("execution-artifact-dialog").showModal();
  });
  $("execution-artifact-close").addEventListener("click", close);
  $("execution-preview-source").addEventListener("click", () => {
    if (!content) return;
    source = !source;
    chart = false;
    $("execution-preview-chart").textContent = "查看图表";
    $("execution-preview-chart").setAttribute("aria-pressed", "false");
    renderInto($("execution-preview"), content, ticket);
    $("execution-preview-source").textContent = source
      ? "查看表格"
      : "查看原文";
  });
  $("execution-preview-chart").addEventListener("click", () => {
    if (!content || !numericColumns(content).length) return;
    chart = !chart;
    source = false;
    $("execution-preview-source").textContent = "查看原文";
    $("execution-preview-chart").textContent = chart ? "查看表格" : "查看图表";
    $("execution-preview-chart").setAttribute("aria-pressed", String(chart));
    renderInto($("execution-preview"), content, ticket);
  });
  return { open, reset, update };
}
