"""The emergency screen's answer, on the adaptive engine's own submit path.

``test_consultation.py`` drives ``build_runtime()``, which is the deterministic
engine: it exercises ``ConsultationEngine.submit``, not the mixin this file
targets.  So the branches below -- the fixed-option answer being recorded, risk
being re-assessed from the landed fact, and the emergency dispatch that follows
-- had no test on the adaptive path at all until this file existed.
"""
import unittest

from medagent.consultation import UserInput
from medagent.consultation.agent import AdaptiveConsultationEngine
from medagent.clinical.knowledge import ContentRepository
from medagent.clinical.reasoner import OrderedQuestionRanker
from medagent.clinical.triage import TriageService
from medagent.core.domain import Phase, RiskLevel
from medagent.core.events import EventStore
from medagent.guards.self_checks import SelfCheckService
from tests.support.scripted import ScriptBackend, cmd, fact


class EmergencyScreenAnswerTests(unittest.TestCase):
    def setUp(self):
        self.backend = ScriptBackend()
        self.events = EventStore()
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=self.events,
            ranker=OrderedQuestionRanker(),
            backend=self.backend,
        )
        self.state, _ = self.engine.create_session()

    def ask_the_screen(self):
        """Land the four opening facts and let the planner ask the screen."""
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("sex", "male", "男"),
                    fact("age_years", 32, "32岁"),
                    fact("chief_complaint", "咳嗽", "咳嗽"),
                    fact("onset", "两天", "两天"),
                ],
            ),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        reply = self.engine.submit(
            self.state.session_id, UserInput("我自己，男，32岁，咳嗽两天")
        )
        self.assertEqual(reply.question_id, "emergency_screen")
        self.backend.commands = []

    def test_a_clicked_yes_is_controller_owned_evidence(self):
        """The option is recorded directly, not inferred from the text.

        ``有任一项`` says nothing a text scan could use, so if the answer were
        being read out of the prose the case would not escalate.  That it does
        escalate is the evidence that the choice itself was written down.
        """
        self.ask_the_screen()
        reply = self.engine.submit(
            self.state.session_id, UserInput("有任一项", "yes")
        )
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(self.state.facts["emergency_screen"].assertion.value, "present")
        self.assertEqual(self.state.risk.level, RiskLevel.EMERGENCY)
        self.assertEqual(self.state.phase, Phase.CLOSED)
        self.assertIsNone(self.state.active_question)
