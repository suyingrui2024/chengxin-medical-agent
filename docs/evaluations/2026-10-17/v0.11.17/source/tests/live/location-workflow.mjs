import { expect } from "@playwright/test";
import path from "node:path";

export async function freshLocationInterview(host, result) {
  const { page, start, send, press, current, control, artifacts } = host;
  await expect(page.getByLabel("界面模式")).toBeEnabled();
  await page.getByLabel("界面模式").selectOption("patient");
  await start();
  result.checks.patient_view_selected =
    (await page.getByLabel("界面模式").inputValue()) === "patient";
  const opening =
    "我自己32岁，头疼两天。没有自行用药，药物过敏史不知道。想按图指出部位，也希望能记录具体区域、左右和范围。";
  await send(opening);
  const answers = {
    consult_for: ["我自己", "self"],
    age: ["32岁"],
    chief_complaint: [opening],
    emergency_screen: ["这些危险情况都没有", "no"],
    onset: ["两天前逐渐开始"],
    trajectory: ["变化不大", "stable"],
    severity: ["不适程度4分，能正常活动"],
    associated_symptoms: ["没有其他不适"],
    medications_taken: ["没有自行用药"],
    allergies: ["药物过敏史不知道"],
    relevant_history: ["没有已知慢性病"],
    patient_goal: ["想整理信息去就医"],
  };
  let firstFact = null,
    revised = false;
  let retries = 0;
  const counts = new Map();
  for (let turn = 0; turn < 24 && current().state.phase !== "summary"; turn++) {
    const data = current();
    if (data.reply.kind === "service_error" && retries++ < 1) {
      await send("重试这一轮", "retry_turn");
      continue;
    }
    expect(["service_error", "emergency", "urgent", "closed"]).not.toContain(
      data.reply.kind,
    );
    if (data.reply.task) {
      expect(data.reply.task.id).toBe("body_location");
      await send("头部右侧太阳穴附近，是一片范围的不适。");
      const spec = current().reply.task?.answer_spec;
      expect(
        spec,
        "Detailed verbal location needs matching registered inputs",
      ).toBeTruthy();
      expect(spec.fields).toEqual(["region", "area", "side", "extent"]);
      const draft = spec.prefill?.answers;
      expect(draft?.region).toBe("head");
      expect(draft?.side).toBe("right");
      expect(draft?.extent).toBe("area");
      expect(
        (draft?.area?.kind === "preset" && draft.area.value === "temple") ||
          (draft?.area?.kind === "text" &&
            String(draft.area.value).includes("太阳穴")),
      ).toBe(true);
      result.checks.location_is_uncommitted_until_patient_confirmation =
        !current().state.facts.symptom_location;
      await expect(page.locator(".location-answer-review")).toBeVisible();
      await page.screenshot({
        path: path.join(artifacts, "live-first-location.png"),
        fullPage: true,
      });
      await press(
        page.getByRole("button", { name: "核对并记录位置", exact: true }),
      );
      firstFact = current().state.facts.symptom_location;
      expect(firstFact?.structured_input.answers.side.value).toBe("right");
      const pending = current().reply;
      await send("刚才位置选错了，请改为左侧太阳穴附近，范围仍是一片。");
      const proposed = current().reply.task?.answer_spec?.prefill;
      expect(proposed?.origin).toBe("revision_proposal");
      expect(proposed?.answers.side).toBe("left");
      expect(current().state.facts.symptom_location.fact_id).toBe(
        firstFact.fact_id,
      );
      await page.screenshot({
        path: path.join(artifacts, "live-location-correction.png"),
        fullPage: true,
      });
      const before = (await control("/__evaluation/metrics")).attempted_calls;
      await press(
        page.getByRole("button", { name: "核对并记录位置", exact: true }),
      );
      const changed = current().state.facts.symptom_location;
      result.checks.revision_confirmed_without_an_extra_model_request =
        (await control("/__evaluation/metrics")).attempted_calls === before;
      result.checks.location_history_and_new_evidence =
        changed.supersedes === firstFact.fact_id &&
        changed.structured_input.revision_of === firstFact.fact_id &&
        changed.structured_input.answers.side.value === "left" &&
        changed.evidence.includes("左右：左侧");
      result.checks.original_question_is_resumed =
        pending.kind === "summary"
          ? current().reply.kind === "summary"
          : current().reply.question_id === pending.question_id &&
            current().reply.message === pending.message;
      result.checks.location_source_is_not_upgraded =
        changed.source === "patient" && changed.quality === "not_assessed";
      revised = true;
    } else {
      const id = data.reply.question_id;
      counts.set(id, (counts.get(id) || 0) + 1);
      expect(counts.get(id), `Repeated question ${id}`).toBeLessThanOrEqual(3);
      expect(answers[id], `Unexpected question ${id}`).toBeTruthy();
      await send(...answers[id]);
    }
  }
  expect(current().state.phase).toBe("summary");
  result.checks.detailed_location_and_revision_completed = Boolean(
    firstFact && revised,
  );
  const item = current().reply.summary.known.find(
    (x) => x.key === "symptom_location",
  );
  result.checks.latest_location_in_summary =
    item?.structured_input?.answers.side.value === "left" &&
    item.structured_input.answers.extent.value === "area";
  result.question_counts = Object.fromEntries(counts);
  result.recovery_attempts = retries;
  await page.screenshot({
    path: path.join(artifacts, "live-location-summary.png"),
    fullPage: true,
  });
  await send("摘要准确", "confirm_summary");
  result.checks.final_summary_confirmed =
    current().state.closed_reason === "confirmed_by_user";
  result.checks.patient_view_kept_through_confirmation =
    (await page.getByLabel("界面模式").inputValue()) === "patient";
}
