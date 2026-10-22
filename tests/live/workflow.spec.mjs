import { _electron as electron, test, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { freshLocationInterview } from "./location-workflow.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

test("synthetic patient completes a native live consultation, emergency routing and service recovery", async () => {
  test.skip(
    !process.env.MEDAGENT_E2E_GATEWAY,
    "Use scripts/evaluation/medical/test_live_desktop.py to enforce the request budget",
  );
  const report = { synthetic_only: true, cases: [] };
  const selected = new Set(
    JSON.parse(
      process.env.MEDAGENT_E2E_CASES ||
        '["native_full_interview","native_emergency_gate","native_service_failure_recovery","native_full_location_interview"]',
    ),
  );
  const artifacts = process.env.MEDAGENT_E2E_ARTIFACTS;
  await mkdir(artifacts, { recursive: true });
  const executablePath = process.env.MEDAGENT_DESKTOP_BINARY;
  const desktop = await electron.launch({
    executablePath,
    args: executablePath ? [] : [root],
    env: { ...process.env },
  });
  const window = await desktop.firstWindow();
  window.setDefaultTimeout(15000);
  const jsErrors = [];
  window.on("pageerror", (error) => jsErrors.push(error.message));
  let current;

  async function control(endpoint, method = "GET") {
    const response = await fetch(process.env.MEDAGENT_E2E_GATEWAY + endpoint, {
      method,
      headers: { Authorization: `Bearer ${process.env.MEDAGENT_E2E_TOKEN}` },
    });
    expect(response.ok).toBe(true);
    return response.json();
  }
  async function start() {
    const response = window.waitForResponse(
      (res) =>
        res.url().endsWith("/api/sessions") &&
        res.request().method() === "POST",
    );
    await window.locator("#new-session").click();
    current = await (await response).json();
    await expect(window.locator("#busy-indicator")).toBeHidden({
      timeout: 60000,
    });
  }
  async function send(text, choice = null) {
    const response = window.waitForResponse(
      (res) =>
        res.url().endsWith("/messages") && res.request().method() === "POST",
      { timeout: 70000 },
    );
    const option = current.reply.quick_replies.find(
      (item) => item.value === choice,
    );
    if (choice && option)
      await window
        .getByRole("button", { name: option.label, exact: true })
        .click();
    else {
      await window.getByRole("textbox", { name: "输入你的描述" }).fill(text);
      await window.getByRole("button", { name: "发送回答" }).click();
    }
    current = await (await response).json();
    await expect(window.locator("#busy-indicator")).toBeHidden({
      timeout: 70000,
    });
    expect(current.reply).toBeTruthy();
  }
  async function edit(key, label, text) {
    await window
      .locator(".fact-row")
      .filter({ hasText: label })
      .getByRole("button", { name: "更正", exact: true })
      .click();
    await window.getByLabel(`更正「${label}」`).fill(text);
    const response = window.waitForResponse(
      (res) =>
        res.request().method() === "PATCH" &&
        res.url().endsWith(`/facts/${key}`),
      { timeout: 70000 },
    );
    await window.getByRole("button", { name: "保存并重新核对" }).click();
    current = await (await response).json();
    await expect(window.locator("#edit-dialog")).toBeHidden({ timeout: 70000 });
  }
  async function press(locator) {
    const response = window.waitForResponse(
      (res) =>
        res.url().endsWith("/messages") && res.request().method() === "POST",
      { timeout: 70000 },
    );
    await locator.click();
    const received = await response;
    expect(received.ok()).toBe(true);
    current = await received.json();
    await expect(window.locator("#busy-indicator")).toBeHidden({
      timeout: 70000,
    });
  }
  async function snapshot() {
    return window.evaluate(
      async (id) => (await fetch(`/api/sessions/${id}/snapshot`)).json(),
      current.state.session_id,
    );
  }
  async function run(id, work) {
    if (!selected.has(id)) return;
    const before = await control("/__evaluation/metrics");
    const result = { id, passed: false, checks: {} };
    try {
      await work(result);
      result.checks.no_renderer_errors = jsErrors.length === 0;
      result.passed = Object.values(result.checks).every(Boolean);
    } catch (error) {
      result.error = String(error.message).replaceAll(
        process.env.MEDAGENT_E2E_TOKEN,
        "[TEST_TOKEN]",
      );
    } finally {
      const after = await control("/__evaluation/metrics");
      result.live_calls = after.attempted_calls - before.attempted_calls;
      if (current?.state?.session_id)
        result.snapshot = await snapshot().catch(() => null);
      report.cases.push(result);
      await writeFile(
        process.env.MEDAGENT_E2E_REPORT,
        JSON.stringify(report, null, 2),
      );
      console.log(
        JSON.stringify({
          case: id,
          passed: result.passed,
          live_calls: result.live_calls,
        }),
      );
    }
  }

  try {
    await expect(window.getByText("本机服务已连接")).toBeVisible();
    await run("native_full_interview", async (result) => {
      await start();
      const initialReport =
        "我自己32岁，咳嗽发烧两天。昨晚用过药物甲，用量不清楚；过敏情况不知道。家里有数字体温计，想按图测一下腋温。";
      await send(initialReport);
      const answers = {
        consult_for: ["我自己", "self"],
        age: ["32岁"],
        chief_complaint: [initialReport],
        emergency_screen: ["这些危险情况都没有", "no"],
        onset: ["两天前逐渐开始"],
        trajectory: ["变化不大", "stable"],
        severity: ["不适程度3分，能正常活动"],
        associated_symptoms: ["还有喉咙痛，没有其他不适"],
        resp_breathing: ["没有气促，能完整说话"],
        resp_cough_details: ["干咳，没有咳血"],
        resp_intake: ["能正常喝水吃饭，排尿正常"],
        medications_taken: ["昨晚用过药物甲，用量不清楚"],
        allergies: ["药物过敏史不知道"],
        relevant_history: ["没有已知慢性病"],
        patient_goal: ["想整理信息去就医"],
      };
      const counts = new Map();
      let readingId = null;
      let retries = 0;
      let ageQuestionsAfterEdit = 0;
      for (
        let turn = 0;
        turn < 24 && current.state.phase !== "summary";
        turn++
      ) {
        if (current.reply.kind === "service_error" && retries++ < 1) {
          await send("重试这一轮", "retry_turn");
          continue;
        }
        expect([
          "service_error",
          "emergency",
          "urgent",
          "closed",
        ]).not.toContain(current.reply.kind);
        if (current.reply.task?.id === "axillary_temperature") {
          await expect(window.locator("#active-task")).toBeVisible();
          await window
            .getByRole("button", { name: "下一步", exact: true })
            .click();
          await expect(
            window.locator("#active-task .task-image"),
          ).toHaveAttribute("src", "/neutral-temperature.svg#step-2");
          await window.screenshot({
            path: path.join(artifacts, "live-self-check.png"),
          });
          const before = await control("/__evaluation/metrics");
          await send("380");
          result.checks.invalid_reading_not_recorded =
            !current.state.facts.temperature_c;
          result.checks.invalid_reading_no_model_call =
            (await control("/__evaluation/metrics")).attempted_calls ===
            before.attempted_calls;
          await send("腋温38.25℃");
          readingId = current.state.facts.temperature_c?.fact_id;
          const beforeEdit = await control("/__evaluation/metrics");
          await edit("age_years", "年龄", "40岁");
          answers.age = ["40岁"];
          answers.chief_complaint = [initialReport.replace("32岁", "40岁")];
          result.checks.age_edit_without_model_call =
            (await control("/__evaluation/metrics")).attempted_calls ===
            beforeEdit.attempted_calls;
          result.checks.reading_kept_after_edit =
            readingId &&
            current.state.facts.temperature_c?.fact_id === readingId;
        } else if (current.reply.task) await send("暂时跳过", "skip");
        else {
          const id = current.reply.question_id;
          if (readingId && id === "age") ageQuestionsAfterEdit++;
          counts.set(id, (counts.get(id) || 0) + 1);
          expect(counts.get(id)).toBeLessThanOrEqual(3);
          expect(answers[id], `Unexpected question ${id}`).toBeTruthy();
          await send(...answers[id]);
        }
      }
      expect(current.state.phase).toBe("summary");
      result.checks.actual_measurement =
        current.state.facts.temperature_c?.value === 38.25;
      result.checks.unknown_preserved_before_edit =
        current.state.facts.allergies?.assertion === "unknown";
      result.checks.severity_collected_from_its_answer =
        current.state.facts.severity?.value === 3 &&
        current.state.facts.severity.evidence.includes("3分");
      result.checks.trajectory_collected_from_its_answer =
        current.state.facts.trajectory?.value === "stable" &&
        current.state.facts.trajectory.evidence.includes("变化不大");
      result.checks.respiratory_details_collected =
        current.state.facts.resp_cough_details?.evidence.includes("干咳") &&
        current.state.facts.resp_breathing?.evidence.includes("没有气促") &&
        current.state.facts.resp_intake?.evidence.includes("喝水");
      result.checks.associated_symptoms_actually_collected =
        String(current.state.facts.associated_symptoms?.value).match(
          /喉咙痛|咽痛|咽部疼痛|嗓子痛/,
        ) !== null &&
        current.state.facts.associated_symptoms?.evidence.includes("喉咙痛");
      const traceBeforeEdit = await snapshot();
      result.question_counts = Object.fromEntries(counts);
      result.checks.no_age_requestion_after_explicit_edit =
        ageQuestionsAfterEdit === 0;
      result.checks.earlier_report_retained_for_model =
        traceBeforeEdit.events.some((event) => {
          if (event.event_type !== "model.requested") return false;
          const context = JSON.parse(event.payload.messages[1].content);
          return (
            context.facts.age_years?.value === 40 &&
            context.earlier_source_turns?.some(
              (source) =>
                source.text === initialReport &&
                Object.values(context.facts).some(
                  (fact) => fact.source_context_id === source.id,
                ),
            )
          );
        });
      const summary = current.reply.summary;
      const reportItem = [
        ...summary.known,
        ...(summary.unknown_details || []),
      ].find((item) => item.key === "medications_taken");
      result.checks.partial_report_retained_in_summary = Boolean(
        reportItem &&
        JSON.stringify(reportItem).includes("药物甲") &&
        JSON.stringify(reportItem).includes("用量不清楚"),
      );
      const beforeProvenance = await control("/__evaluation/metrics");
      const knownReport = window.locator(
        ".current-summary .summary-item[data-fact-key='medications_taken']",
      );
      const reportNode = (await knownReport.count())
        ? knownReport
        : window.locator(
            ".current-summary .unknown-report[data-fact-key='medications_taken']",
          );
      if (await knownReport.count())
        await knownReport.locator(".summary-provenance > summary").click();
      const sourceDetails = reportNode.locator(".summary-source-context");
      if (await sourceDetails.count())
        await sourceDetails.locator("summary").click();
      await expect(
        reportNode
          .locator("blockquote")
          .filter({ hasText: "用量不清楚" })
          .first(),
      ).toBeVisible();
      result.checks.read_sources_without_model_call =
        (await control("/__evaluation/metrics")).attempted_calls ===
        beforeProvenance.attempted_calls;
      await window.screenshot({
        path: path.join(artifacts, "live-partial-report.png"),
      });
      const beforeSummaryEdit = await control("/__evaluation/metrics");
      await edit("allergies", "过敏信息", "青霉素过敏");
      result.checks.summary_edit_without_model_call =
        (await control("/__evaluation/metrics")).attempted_calls ===
        beforeSummaryEdit.attempted_calls;
      await expect(window.locator(".summary-card").last()).toContainText(
        "青霉素过敏",
      );
      await window.screenshot({
        path: path.join(artifacts, "live-corrected-summary.png"),
      });
      await send("摘要准确", "confirm_summary");
      result.checks.confirmed =
        current.state.closed_reason === "confirmed_by_user";
      result.checks.age_corrected = current.state.facts.age_years.value === 40;
      result.checks.patient_source_and_quality =
        current.state.facts.temperature_c.source === "patient" &&
        current.state.facts.temperature_c.quality === "not_assessed";
      await window.getByLabel("界面模式").selectOption("developer");
      await window.getByRole("button", { name: "查看本次运行记录" }).click();
      const exportPath = path.join(artifacts, "synthetic-session-export.json");
      await desktop.evaluate(({ BrowserWindow }, target) => {
        globalThis.medagentEvaluationDownload = "waiting";
        BrowserWindow.getAllWindows()[0].webContents.session.once(
          "will-download",
          (_event, item) => {
            item.setSavePath(target);
            item.once("done", (_doneEvent, state) => {
              globalThis.medagentEvaluationDownload = state;
            });
          },
        );
      }, exportPath);
      await window.getByRole("button", { name: "导出本次记录" }).click();
      await expect
        .poll(
          () => desktop.evaluate(() => globalThis.medagentEvaluationDownload),
          { timeout: 15000 },
        )
        .toBe("completed");
      const exported = JSON.parse(await readFile(exportPath, "utf8"));
      const before = await control("/__evaluation/metrics");
      await window.locator('.nav-item[data-view="history"]').click();
      await window
        .locator(".history-card")
        .filter({ hasText: "咳嗽" })
        .first()
        .click();
      await expect(window.locator("#composer")).toBeHidden();
      result.checks.history_without_model_call =
        (await control("/__evaluation/metrics")).attempted_calls ===
        before.attempted_calls;
      result.checks.export_completed =
        exported.case?.session_id === current.state.session_id &&
        exported.case?.facts.age_years.value === 40;
      result.recovery_attempts = retries;
    });
    await run("native_emergency_gate", async (result) => {
      await start();
      const before = await control("/__evaluation/metrics");
      await send("他现在叫不醒");
      result.checks.emergency_route =
        current.reply.kind === "emergency" &&
        current.state.closed_reason === "emergency_route";
      result.checks.no_model_call =
        (await control("/__evaluation/metrics")).attempted_calls ===
        before.attempted_calls;
      result.checks.no_model_request_event = !(await snapshot()).events.some(
        (event) => event.event_type === "model.requested",
      );
      await expect(window.locator("#messages")).toContainText("120");
    });
    await run("native_service_failure_recovery", async (result) => {
      await start();
      await control("/__evaluation/fail-next", "POST");
      const text = "我自己29岁，头痛一天，没有自行用药，药物过敏不知道。";
      await send(text);
      result.checks.service_error_visible =
        current.reply.kind === "service_error";
      await expect(
        window.getByRole("button", { name: "重试这一轮", exact: true }),
      ).toBeVisible();
      await send("重试这一轮", "retry_turn");
      result.checks.recovered = current.reply.kind === "question";
      result.checks.correct_original_facts =
        current.state.facts.age_years?.value === 29 &&
        current.state.facts.allergies?.assertion === "unknown";
      const trace = await snapshot();
      result.checks.original_input_reused =
        trace.events.filter(
          (event) =>
            event.event_type === "turn.user_message" &&
            event.payload.text === text,
        ).length === 2;
    });
    await run("native_full_location_interview", async (result) => {
      await freshLocationInterview(
        {
          page: window,
          start,
          send,
          press,
          current: () => current,
          control,
          artifacts,
        },
        result,
      );
    });
  } finally {
    await desktop.close();
  }
  expect(
    report.cases.filter((item) => !item.passed).map((item) => item.id),
  ).toEqual([]);
});
