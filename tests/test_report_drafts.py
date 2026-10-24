import json
import threading
import unittest
from urllib.request import Request, urlopen

from medagent.background_review import BackgroundReviewCoordinator, ReviewJob
from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.events import EventStore
from medagent.handoff import build_handoff
from medagent.report_drafts import parse_report_draft
from medagent.runtime import build_runtime
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
