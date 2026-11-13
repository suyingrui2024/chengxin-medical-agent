// Plot only the exact, bounded table snapshot supplied by the host. No inference,
// aggregation, missing-value filling or evaluation of cell text.
export function numericCell(raw) {
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return null;
  const value = Number(text);
  if (!Number.isFinite(value)) return null;
  if (Number.isInteger(value) && !Number.isSafeInteger(value)) return null;
  if (value === 0 && /[1-9]/.test(text.split(/e/i)[0])) return null;
  return value;
}

export function numericColumns(table) {
  if (table.kind !== "table" || table.rows.length < 2) return [];
  const columns = [];
  for (let index = 0; index < table.columns; index++) {
    const raw = table.rows.slice(1).map((row) => row[index]);
    const values = raw.map(numericCell);
    if (values.some((value) => value === null)) continue;
    columns.push({
      index,
      label: table.rows[0][index] || `第 ${index + 1} 列`,
      raw,
      values,
    });
  }
  return columns;
}

export function chartSeries(table, requestedColumn) {
  const columns = numericColumns(table);
  const column =
    columns.find((item) => item.index === requestedColumn) ||
    columns.find((item) => item.index > 0) ||
    columns[0];
  if (!column) return null;
  const low = Math.min(0, ...column.values);
  const high = Math.max(0, ...column.values);
  const scale = Math.max(Math.abs(low), Math.abs(high)) || 1;
  const span = high / scale - low / scale || 1;
  const position = (value) => ((value / scale - low / scale) / span) * 100;
  const zero = position(0);
  return {
    column,
    columns,
    labelColumn: column.index > 0 ? table.rows[0][0] || "标记" : "行",
    zero,
    low: low === 0 ? "0" : column.raw[column.values.indexOf(low)],
    high: high === 0 ? "0" : column.raw[column.values.indexOf(high)],
    points: column.values.map((value, index) => ({
      label:
        (column.index > 0 && table.rows[index + 1][0]) || `第 ${index + 2} 行`,
      raw: column.raw[index],
      value,
      left: Math.min(zero, position(value)),
      width: Math.abs(position(value) - zero),
    })),
  };
}

function node(tag, className, text) {
  const result = document.createElement(tag);
  result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

export function renderArtifactChart(table, columnIndex, onColumnChange) {
  const series = chartSeries(table, columnIndex);
  if (!series) return null;
  const figure = node("figure", "artifact-chart");
  const control = node("label", "artifact-chart-control", "数值列");
  const select = node("select", "artifact-chart-column");
  select.setAttribute("aria-label", "图表数值列");
  for (const column of series.columns)
    select.append(
      new Option(`${column.label} · 第 ${column.index + 1} 列`, column.index),
    );
  select.value = String(series.column.index);
  select.addEventListener("change", () => onColumnChange(Number(select.value)));
  control.append(select);
  const axis = node("div", "artifact-chart-axis");
  axis.setAttribute("aria-hidden", "true");
  const range = node("div", "artifact-chart-range");
  range.append(node("span", "", series.low), node("span", "", series.high));
  axis.append(
    node("span", "artifact-chart-label-heading", series.labelColumn),
    range,
    node("span", ""),
  );
  const points = node("ul", "artifact-chart-points");
  points.setAttribute(
    "aria-label",
    `${series.column.label} · ${series.labelColumn} · 原行顺序`,
  );
  for (const point of series.points) {
    const row = node("li", "artifact-chart-row");
    row.title = `${point.label}: ${point.raw}`;
    const label = node("span", "artifact-chart-label", point.label);
    const track = node("span", "artifact-chart-track");
    track.setAttribute("aria-hidden", "true");
    const baseline = node("span", "artifact-chart-zero");
    baseline.style.left = `${series.zero}%`;
    const bar = node(
      "span",
      `artifact-chart-bar${point.value < 0 ? " is-negative" : ""}`,
    );
    bar.style.left = `${point.left}%`;
    bar.style.width = `${point.width}%`;
    track.append(baseline, bar);
    row.append(label, track, node("span", "artifact-chart-value", point.raw));
    points.append(row);
  }
  const scroll = node("div", "artifact-chart-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "图表数据，按原行顺序");
  scroll.append(axis, points);
  figure.append(
    control,
    scroll,
    node(
      "figcaption",
      "artifact-chart-caption",
      `${table.rows_truncated ? "仅显示当前预览的 " : ""}${series.points.length} 条数据 · 原行顺序 · 零点基线`,
    ),
  );
  return figure;
}
