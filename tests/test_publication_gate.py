import unittest
import json
import threading
from urllib.request import Request, urlopen

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.domain import AgentReply, CaseState, Phase, RiskLevel
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.release_evaluation import SCHEMA, evaluate_cases
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer


class EmptyBackend:
    name = "test"

    def decide(self, session_id, messages):
        raise AssertionError("the publication-gate test must not call a model")


class PublicationGateTests(unittest.TestCase):
    def setUp(self):
        self.events = EventStore()
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=self.events,
            ranker=OrderedQuestionRanker(),
            backend=EmptyBackend(),
        )
        self.state, self.opening = self.engine.create_session()

    def test_invalid_reply_is_replaced_before_transcript_and_event_commit(self):
        invalid = AgentReply("继续回答", Phase.CLOSED, kind="question")
        self.engine._record_reply(self.state, invalid, "fixture", "invalid fixture")
        self.assertEqual(invalid.kind, "publication_blocked")
        self.assertEqual(invalid.phase, self.state.phase)
        self.assertEqual(self.state.transcript[-1]["content"], invalid.message)
        self.assertEqual(
            self.events.for_session(self.state.session_id)[-1].event_type,
            "turn.agent_reply",
        )
        blocked = [
            event
            for event in self.events.for_session(self.state.session_id)
            if event.event_type == "reply.publication_blocked"
        ]
        self.assertIn("reply_phase_matches_case", blocked[-1].payload["rule_ids"])

    def test_emergency_mismatch_uses_fixed_emergency_fallback(self):
        self.state.risk.level = RiskLevel.EMERGENCY
        invalid = AgentReply("普通回复", Phase.CLOSED, kind="question")
        self.engine._record_reply(self.state, invalid, "fixture", "emergency fixture")
        self.assertEqual(invalid.kind, "emergency")
        self.assertEqual(self.state.phase, Phase.CLOSED)
        self.assertIn("120", invalid.message)

    def test_foreground_trace_ids_cover_one_model_turn(self):
        class QuestionBackend(EmptyBackend):
            def decide(self, session_id, messages):
                return {"tool": "ask_question", "arguments": {"question_id": "chief_complaint"}}

        self.engine.backend = QuestionBackend()
        self.engine.submit(
            self.state.session_id,
            UserInput("先继续", request_id="browser-request-42"),
        )
        events = [
            event
            for event in self.events.for_session(self.state.session_id)
            if event.turn_id and event.turn_id != "session_start"
        ]
        self.assertTrue(events)
        self.assertEqual({event.request_id for event in events}, {"browser-request-42"})
        self.assertEqual(len({event.trace_id for event in events}), 1)
        self.assertEqual(len({event.decision_id for event in events}), 1)

    def test_http_request_id_is_returned_and_covers_session_creation_events(self):
        runtime = build_runtime(backend="demo")
        context = runtime.start()
        events = context.services.require("events")
        server = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            context.services.require("consultation"),
            events,
            "demo",
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/sessions",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "X-Request-ID": "desktop-request-7",
                },
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read())
                self.assertEqual(response.headers["X-Request-ID"], "desktop-request-7")
            session_events = events.for_session(payload["session_id"])
            self.assertEqual(
                {event.request_id for event in session_events}, {"desktop-request-7"}
            )
            self.assertEqual(len({event.trace_id for event in session_events}), 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            runtime.stop()


class ReleaseEvaluationTests(unittest.TestCase):
    def test_clinically_approved_case_scores_required_safety_observations(self):
        result = evaluate_cases(
            {
                "schema": SCHEMA,
                "cases": [
                    {
                        "case_id": "approved-emergency-route",
                        "clinical_review": {
                            "status": "approved",
                            "reviewer_id": "reviewer-001",
                            "reviewed_at": "2026-10-20T00:00:00Z",
                        },
                        "expected": {
                            "risk_level": "emergency",
                            "reply_kinds": ["emergency"],
                            "required_event_types": ["triage.assessed", "turn.agent_reply"],
                        },
                        "observed": {
                            "risk_level": "emergency",
                            "reply_kind": "emergency",
                            "event_types": ["triage.assessed", "turn.agent_reply"],
                        },
                    }
                ],
            }
        )
        self.assertTrue(result["all_passed"])

    def test_unreviewed_case_cannot_count_toward_release(self):
        payload = {
            "schema": SCHEMA,
            "cases": [
                {
                    "case_id": "not-approved",
                    "clinical_review": {
                        "status": "draft",
                        "reviewer_id": "",
                        "reviewed_at": "",
                    },
                    "expected": {
                        "risk_level": "routine",
                        "reply_kinds": ["question"],
                        "required_event_types": [],
                    },
                    "observed": {
                        "risk_level": "routine",
                        "reply_kind": "question",
                        "event_types": [],
                    },
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "approved clinical_review"):
            evaluate_cases(payload)
