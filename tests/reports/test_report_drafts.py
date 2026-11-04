import json
import threading
import unittest
from urllib.request import Request, urlopen

from medagent.review.background_review import BackgroundReviewCoordinator, ReviewJob
from medagent.core.domain import Assertion, CaseState, Fact, Source
from medagent.core.events import EventStore
from medagent.reports.handoff import build_handoff
from medagent.reports.report_drafts import parse_report_draft
from tests.support.reports_case import _fact
from medagent.server.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer


class ReportDraftTests(unittest.TestCase):
    def test_text_rows_retain_their_original_excerpt(self):
        draft = parse_report_draft("报告日期 2026-10-20\n空腹血糖 4.2 mmol/L 3.9-6.1")
        self.assertEqual(draft["source_format"], "text")
        self.assertEqual(draft["items"][0]["name"], "空腹血糖")
        self.assertEqual(draft["items"][0]["reported_value"], "4.2")
        self.assertEqual(draft["items"][0]["reference_range"], "3.9-6.1")
        self.assertIn("空腹血糖", draft["items"][0]["source_excerpt"])
        self.assertEqual(draft["missing_context"], [])

    def test_csv_requires_project_and_value_columns_and_never_interprets_them(self):
        draft = parse_report_draft(
            "项目,结果,单位,参考范围\n血红蛋白,130,g/L,115-150\n", "csv"
        )
        item = draft["items"][0]
        self.assertEqual(item["name"], "血红蛋白")
        self.assertEqual(item["status"], "needs_confirmation")
        self.assertIn("未判断异常原因", draft["limitations"][0])

    def test_unrecognised_content_stays_a_missing_context_not_a_fact(self):
        draft = parse_report_draft("这是一张照片中的化验单，请帮我看。")
        self.assertEqual(draft["items"], [])
        self.assertIn("可识别的项目、数值与参考范围", draft["missing_context"])

    def test_handoff_keeps_medication_and_allergy_as_patient_provided_facts(self):
        state = CaseState("handoff-test")
        state.record(
            Fact(
                "medications_taken",
                "自行服用对乙酰氨基酚",
                Assertion.PRESENT,
                Source.PATIENT,
                "turn-1",
            )
        )
        state.record(
            Fact("allergies", "青霉素过敏", Assertion.PRESENT, Source.PATIENT, "turn-1")
        )
        state.report_items.append(
            {
                "report_item_id": "draft:row-1",
                "name": "空腹血糖",
                "reported_value": "4.2",
                "unit": "mmol/L",
                "reference_range": "3.9-6.1",
                "status": "confirmed_by_patient",
            }
        )

        handoff = build_handoff(state)

        self.assertEqual(handoff["schema"], "patient-handoff/v2")
        self.assertEqual(
            {row["label"] for row in handoff["facts"]}, {"已报告用药与处理", "过敏信息"}
        )
        self.assertEqual(handoff["report_items"][0]["name"], "空腹血糖")
        self.assertNotIn("diagnosis", handoff)

    def test_review_snapshot_receives_only_confirmed_structured_report_fields(self):
        state = CaseState("review-report-test")
        state.report_items.append(
            {
                "report_item_id": "draft:row-1",
                "name": "空腹血糖",
                "reported_value": "4.2",
                "unit": "mmol/L",
                "reference_range": "3.9-6.1",
                "source_excerpt": "不应出现在确认后的项目中",
                "status": "confirmed_by_patient",
            }
        )
        coordinator = BackgroundReviewCoordinator(EventStore())
        try:
            snapshot = coordinator._snapshot(
                state,
                ReviewJob("job", None, state.session_id, state.revision, None),
            )
        finally:
            coordinator.close()

        self.assertEqual(snapshot["patient_confirmed_report_items"][0]["name"], "空腹血糖")
        self.assertNotIn(
            "source_excerpt", snapshot["patient_confirmed_report_items"][0]
        )

    def test_report_api_requires_confirmation_before_exposing_a_report_item(self):
        runtime = build_runtime(backend="demo")
        context = runtime.start()
        server = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            context.services.require("consultation"),
            context.services.require("events"),
            "demo",
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def request(path, method="GET", payload=None):
            body = json.dumps(payload).encode() if payload is not None else None
            headers = {"Content-Type": "application/json"} if body else {}
            with urlopen(Request(base + path, data=body, headers=headers, method=method), timeout=2) as response:
                return json.loads(response.read())

        try:
            session_id = request("/api/sessions", "POST", {})["session_id"]
            draft = request(
                f"/api/sessions/{session_id}/report-drafts",
                "POST",
                {"text": "报告日期 2026-10-20\n空腹血糖 4.2 mmol/L 3.9-6.1"},
            )["draft"]
            before = request(f"/api/sessions/{session_id}/handoff")["handoff"]
            self.assertEqual(before["report_items"], [])
            confirmed = request(
                f"/api/sessions/{session_id}/report-drafts/{draft['draft_id']}/confirm",
                "POST",
                {"source_item_id": draft["items"][0]["source_item_id"]},
            )
            self.assertEqual(confirmed["item"]["status"], "confirmed_by_patient")
            handoff = request(f"/api/sessions/{session_id}/handoff")["handoff"]
            self.assertEqual(handoff["report_items"][0]["reported_value"], "4.2")
            event_payloads = json.dumps(
                request(f"/api/sessions/{session_id}/events")["events"], ensure_ascii=False
            )
            self.assertNotIn("空腹血糖 4.2", event_payloads)
            self.assertNotIn("source_excerpt", event_payloads)
            removed = request(
                f"/api/sessions/{session_id}/report-items/{confirmed['item']['report_item_id']}",
                "DELETE",
            )
            self.assertTrue(removed["deleted"])
            self.assertEqual(request(f"/api/sessions/{session_id}/handoff")["handoff"]["report_items"], [])
        finally:
            server.shutdown()
            worker.join(timeout=2)
            server.server_close()
            runtime.stop()


if __name__ == "__main__":
    unittest.main()


class RealWorldReportFormatTests(unittest.TestCase):
    """使用体验评测 R2-1（2026-11-04）：真实检验单最常见行格式。

    修复前两种格式都提取为零——括注参考被 （参考 …） 挡住，血压比值
    不是独立数值。excerpt 必须保留患者粘贴的原文。
    """

    def test_annotated_reference_line_parses(self):
        draft = parse_report_draft("空腹血糖 5.6 mmol/L（参考 3.9-6.1）")
        item = draft["items"][0]
        self.assertEqual(item["name"], "空腹血糖")
        self.assertEqual(item["reported_value"], "5.6")
        self.assertEqual(item["unit"], "mmol/L")
        self.assertEqual(item["reference_range"], "3.9-6.1")
        self.assertIn("（参考 3.9-6.1）", item["source_excerpt"])

    def test_annotated_single_bound_and_half_width_parens_parse(self):
        draft = parse_report_draft("总胆固醇 6.2 mmol/L (参考 <5.2)")
        item = draft["items"][0]
        self.assertEqual(item["name"], "总胆固醇")
        self.assertEqual(item["reference_range"], "<5.2")

    def test_blood_pressure_pair_parses_without_reference(self):
        draft = parse_report_draft("血压 128/84 mmHg")
        item = draft["items"][0]
        self.assertEqual(item["name"], "血压")
        self.assertEqual(item["reported_value"], "128/84")
        self.assertEqual(item["unit"], "mmHg")
        self.assertEqual(item["reference_range"], "")

    def test_the_reviewers_original_report_extracts_three_items(self):
        draft = parse_report_draft(
            "检验报告日期 2026-11-01\n"
            "空腹血糖 5.6 mmol/L（参考 3.9-6.1）\n"
            "总胆固醇 6.2 mmol/L（参考 <5.2）\n"
            "血压 128/84 mmHg"
        )
        self.assertEqual(len(draft["items"]), 3)
        self.assertEqual(draft["missing_context"], [])


class HandoffPatientLanguageTests(unittest.TestCase):
    """使用体验评测 R2-2（2026-11-04）：交接摘要不说内部语言。

    枚举值渲染为 questions.json 的患者文案，None 按断言说明原因——
    'self'/'male'/stable/null 这样的内部值绝不直接出现在患者眼前。
    """

    def _state_with_enums(self):
        state = CaseState("handoff-language-test")
        state.record(_fact("consult_for", "self", Assertion.PRESENT))
        state.record(_fact("sex", "male", Assertion.PRESENT))
        state.record(_fact("trajectory", "stable", Assertion.PRESENT))
        state.record(_fact("medications_taken", None, Assertion.UNKNOWN))
        state.record(_fact("emergency_screen", None, Assertion.ABSENT))
        return state

    def test_enum_values_render_patient_labels(self):
        handoff = build_handoff(self._state_with_enums())
        values = {row["label"]: row["value"] for row in handoff["facts"]}
        self.assertEqual(values["咨询对象"], "本人")
        self.assertEqual(values["性别"], "男")
        self.assertEqual(values["变化趋势"], "变化不大")

    def test_unknown_facts_go_to_needs_confirmation_not_null_rows(self):
        # 未知项本就路由到"仍待补充"——那是正确的患者语言；
        # 这里钉住它不再退回 null 行。
        handoff = build_handoff(self._state_with_enums())
        values = {row["label"]: row["value"] for row in handoff["facts"]}
        self.assertEqual(values["急症筛查"], "已明确没有")
        self.assertIn("已报告用药与处理", [x["label"] for x in handoff["needs_confirmation"]])
        encoded = json.dumps(handoff, ensure_ascii=False)
        self.assertNotIn('"value": null', encoded)
        self.assertNotIn('"value": "self"', encoded)
        self.assertNotIn('"value": "male"', encoded)
