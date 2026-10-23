"""HTTP surface of the longitudinal loop: the follow-up due list and the
outcome endpoint that were designed in followups.py but never routed.

Before this, a plan could be scheduled and displayed, yet no client could
list due plans or record an outcome, so worsened -> re-triage linkage was
unreachable in the real product.
"""
from __future__ import annotations

import http.client
import json
import threading
import unittest

from medagent.consultation import UserInput
from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer

ANSWERS = {
    "consult_for": ("我自己", "self"),
    "age": ("32岁", None),
    "chief_complaint": ("腹痛两小时", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("两小时前突然出现", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("4分", None),
    "associated_symptoms": ("没有", None),
    "medications_taken": ("没有", None),
    "allergies": ("不知道", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想整理信息去就医", None),
}

TOKEN = "test-token"


class FollowUpApiTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        events = self.context.services.require("events")
        self.state, self.reply = self.engine.create_session()
        self._drive_to_confirmed_close()
        self.server = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            self.engine,
            events,
            access_token=TOKEN,
        )
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.worker.join(timeout=5)
        self.server.server_close()
        self.host.stop()

    def _drive_to_confirmed_close(self):
        reply = self.reply
        for _ in range(25):
            if reply.kind == "summary":
                break
            if reply.kind == "self_check":
                reply = self.engine.submit(
                    self.state.session_id, UserInput("暂时跳过", "skip")
                )
                continue
            text, choice = ANSWERS[reply.question_id]
            reply = self.engine.submit(
                self.state.session_id, UserInput(text, choice)
            )
        else:
            raise AssertionError(f"did not reach summary; last kind={reply.kind}")
        reply = self.engine.submit(
            self.state.session_id, UserInput("确认", "confirm_summary")
        )
        self.assertEqual(reply.kind, "closed")
        self.assertEqual(self.state.follow_up.get("status"), "scheduled")

    def _request(self, method, path, payload=None):
        connection = http.client.HTTPConnection(
            *self.server.server_address, timeout=5
        )
        try:
            connection.request(
                method,
                path,
                body=json.dumps(payload) if payload is not None else None,
                headers={
                    "X-MedAgent-Token": TOKEN,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            connection.close()

    def test_due_list_exposes_scheduled_plan(self):
        status, body = self._request("GET", "/api/follow-ups")
        self.assertEqual(status, 200)
        entries = [
            item
            for item in body["follow_ups"]
            if item["session_id"] == self.state.session_id
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["plan"]["status"], "scheduled")
        self.assertEqual(entries[0]["plan"]["kind"], "routine_checkin")
        self.assertTrue(entries[0]["plan"]["due_at"])

    def test_recording_worsened_outcome_links_retriage_session(self):
        status, body = self._request(
            "POST",
            f"/api/sessions/{self.state.session_id}/follow-up/outcome",
            {"outcome": "worsened", "note": "疼痛加重"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["requires_retriage"])
        self.assertTrue(body["new_session_id"])
        self.assertEqual(body["follow_up"]["status"], "completed")
        self.assertEqual(body["follow_up"]["outcome"], "worsened")
        new_state = self.engine.state(body["new_session_id"])
        self.assertEqual(
            new_state.runtime_snapshot.get("follow_up_of"), self.state.session_id
        )
        # A completed plan rejects a second outcome.
        status, _ = self._request(
            "POST",
            f"/api/sessions/{self.state.session_id}/follow-up/outcome",
            {"outcome": "recovered"},
        )
        self.assertEqual(status, 400)

    def test_invalid_outcome_and_unknown_session(self):
        status, _ = self._request(
            "POST",
            f"/api/sessions/{self.state.session_id}/follow-up/outcome",
            {"outcome": "fine"},
        )
        self.assertEqual(status, 400)
        status, _ = self._request(
            "POST",
            "/api/sessions/no-such-session/follow-up/outcome",
            {"outcome": "recovered"},
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
