// Actual managed workspace bytes and host preview, without starting an agent turn.
import assert from "node:assert/strict";
import { writeFile, realpath } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { expect } from "@playwright/test";
import { setInterfaceMode } from "./interface-mode.mjs";

export async function verifyNativeArtifactChart(page, executionRoot) {
  const snapshot = await page.evaluate(async () => {
    const response = await fetch("/api/execution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok)
      throw new Error(`Create chart workspace: ${response.status}`);
    return response.json();
  });
  assert.equal(
    path.dirname(await realpath(snapshot.directory)),
    await realpath(executionRoot),
  );
  const csv = "样本,质量 (g)\nA,1250\nB,2000\nC,750\n";
  await writeFile(path.join(snapshot.directory, "measured.csv"), csv, "utf8");
  await setInterfaceMode(page, "developer");
  await page.getByRole("button", { name: "执行工作台", exact: true }).click();
  await page.locator("#execution-refresh").click();
  await expect(
    page.locator(`#execution-workspace option[value="${snapshot.id}"]`),
  ).toHaveCount(1);
  await page.locator("#execution-workspace").selectOption(snapshot.id);
  await expect(
    page.locator('#execution-quick-files button[data-path="measured.csv"]'),
  ).toHaveCount(1);
  await page.locator("#execution-quick-look > summary").click();
  await page
    .getByRole("button", { name: "快速查看：measured.csv", exact: true })
    .click();
  await page.getByRole("button", { name: "查看图表", exact: true }).click();
  await expect(
    page.locator("#execution-preview .artifact-chart-value"),
  ).toHaveText(["1250", "2000", "750"]);
  await expect(page.locator("#execution-preview-meta")).toHaveAttribute(
    "title",
    `SHA256 ${createHash("sha256").update(csv).digest("hex")}`,
  );
  const observed = await page.evaluate(
    async (id) => (await (await fetch(`/api/execution/${id}`)).json()).turns,
    snapshot.id,
  );
  assert.equal(observed.length, 0, "Preview must not create an agent turn");
  return { passed: true, actual_csv: true, rows: 3, agent_turns: 0 };
}
