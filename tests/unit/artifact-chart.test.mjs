import { test } from "node:test";
import assert from "node:assert/strict";
import {
  numericCell,
  numericColumns,
  chartSeries,
} from "../../src/medagent/web/artifact-chart-ui.js";
const table = (rows) => ({ kind: "table", rows, columns: rows[0].length });

test("chart cells accept finite decimal numbers without coercing missing, formatted or unsafe values", () => {
  for (const raw of [
    "",
    " ",
    "1,234",
    "0x10",
    "NaN",
    "Infinity",
    "=1+1",
    "12 kg",
    "9007199254740993",
    "1e309",
    "1e-400",
    undefined,
  ])
    assert.equal(numericCell(raw), null, String(raw));
  for (const [raw, expected] of [
    ["0", 0],
    [" -2.50 ", -2.5],
    [".75", 0.75],
    ["1.25e3", 1250],
    ["0e10", 0],
    ["5e-324", 5e-324],
  ])
    assert.equal(numericCell(raw), expected);
});
test("bars retain original row order, exact labels and values and include the zero baseline", () => {
  const input = table([
    ["样本", "质量 (g)"],
    ["B", "2000"],
    ["A", "1250"],
    ["C", "750"],
  ]);
  const result = chartSeries(input);
  assert.deepEqual(
    result.points.map((p) => [p.label, p.raw, p.width]),
    [
      ["B", "2000", 100],
      ["A", "1250", 62.5],
      ["C", "750", 37.5],
    ],
  );
  assert.equal(result.zero, 0);
  assert.equal(result.labelColumn, "样本");
  assert.equal(result.low, "0");
  assert.equal(result.high, "2000");
});
test("negative, positive and zero bars share one scale without magnitude distortion", () => {
  const result = chartSeries(
    table([
      ["name", "delta"],
      ["negative", "-2"],
      ["positive", "4"],
      ["zero", "0"],
    ]),
  );
  assert(Math.abs(result.zero - 100 / 3) < 1e-10);
  assert(Math.abs(result.points[0].width - 100 / 3) < 1e-10);
  assert(Math.abs(result.points[1].width - 200 / 3) < 1e-10);
  assert.equal(result.points[2].width, 0);
  for (const p of result.points)
    assert(p.left >= 0 && p.width >= 0 && p.left + p.width <= 100.000001);
});
test("missing and ragged numeric columns stay unavailable and are never filled with zero", () => {
  assert.deepEqual(
    numericColumns(
      table([
        ["label", "value"],
        ["A", "2"],
        ["B", ""],
      ]),
    ),
    [],
  );
  assert.deepEqual(
    numericColumns(table([["label", "value"], ["A", "2"], ["B"]])),
    [],
  );
  assert.equal(chartSeries(table([["label", "value"]])), null);
});
test("zero-only and very small signed series remain finite and preserve the source snapshot", () => {
  const input = table([
    ["name", "delta", "other"],
    ["A", "0", "-1e-300"],
    ["B", "0", "2e-300"],
  ]);
  const saved = JSON.stringify(input);
  const zero = chartSeries(input, 1);
  assert.equal(zero.zero, 0);
  assert(zero.points.every((p) => p.width === 0));
  const tiny = chartSeries(input, 2);
  assert(
    tiny.points.every(
      (p) => Number.isFinite(p.left) && Number.isFinite(p.width),
    ),
  );
  assert.equal(JSON.stringify(input), saved);
});
