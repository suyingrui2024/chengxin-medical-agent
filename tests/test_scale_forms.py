"""Scale form flow: model proposes, patient answers, host scores."""

from copy import deepcopy
import unittest

from medagent.consultation import UserInput
from medagent.events import EventStore
from medagent.agent import AdaptiveConsultationEngine
from medagent.extensions.builtin import load_registry
from medagent.extensions.scale_forms import ScaleSpecError, scale_form_spec
from medagent.knowledge import ContentRepository
from medagent.patient_calculations import confirmed_parameters, request_draft
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService

SCALE_ID = "calculate.phq8"
REQUEST = "帮我做个 PHQ-8 抑郁自评"


class Backend:
    name = "fixture"

    def __init__(self):
        self.commands, self.calls = [], []

    def decide(self, identity, messages):
        self.calls.append(deepcopy(messages))
        return self.commands.pop(0)


def make_engine():
    backend = Backend()
    registry = load_registry()
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(registry),
        triage=TriageService(),
        self_checks=SelfCheckService(registry),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    return engine, backend, registry


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def test_draft_starts_empty_and_ignores_model_arguments(self):
        # A model that tries to pre-fill scores must not reach the card.
        draft = request_draft(
            self.registry,
            SCALE_ID,
            {"p1": 3, "p2": 3},
            REQUEST,
            REQUEST,
            "t1",
        )
        self.assertEqual(draft["input"], {})
        self.assertEqual(draft["original_input"], {})
        self.assertEqual(draft["form_kind"], "scale_form/v1")
        self.assertEqual(len(draft["items"]), 8)
        self.assertEqual(draft["progress"], {"answered": 0, "total": 8})

    def test_request_must_name_the_scale(self):
        with self.assertRaises(ValueError):
            request_draft(
                self.registry, SCALE_ID, {}, "帮我做个量表", "帮我做个量表", "t1"
            )

    def test_request_must_express_an_intent(self):
        with self.assertRaises(ValueError):
            request_draft(self.registry, SCALE_ID, {}, "PHQ-8", "PHQ-8", "t1")

    def test_cancel_wording_is_refused(self):
        text = "算了不做了，PHQ-8 先不算"
        with self.assertRaises(ValueError):
            request_draft(self.registry, SCALE_ID, {}, text, text, "t1")

    def test_quote_must_be_verbatim(self):
        with self.assertRaises(ValueError):
            request_draft(
                self.registry, SCALE_ID, {}, "做个 PHQ-9", REQUEST, "t1"
            )

    def test_common_phrasings_are_accepted(self):
        # Patients rarely say 「量表」; these are the phrasings that showed
        # up while driving real consultations.
        for text in (
            "帮我算 PHQ-8",
            "帮我做 PHQ-8 抑郁量表",
            "评估一下 PHQ-8",
            "测一下 PHQ-8",
        ):
            draft = request_draft(self.registry, SCALE_ID, {}, text, text, "t1")
            self.assertEqual(draft["capability_id"], SCALE_ID, text)

    def test_confirmed_parameters_rejects_stale_version(self):
        draft = request_draft(
            self.registry, SCALE_ID, {}, REQUEST, REQUEST, "t1"
        )
        stale = dict(draft, version="0.0.1")
        with self.assertRaises(ValueError):
            confirmed_parameters(self.registry, stale, {"p1": 1})


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()
        self.manifest = next(
            item for item in self.registry.descriptions() if item["id"] == SCALE_ID
        )

    def test_patient_scale_rejects_threshold_items(self):
        # The patient card can only render radio groups; a measured-number
        # item would be silently unanswerable.
        manifest = deepcopy(self.manifest)
        manifest["scale"]["items"].append(
            {
                "id": "temp",
                "label": "体温",
                "kind": "threshold_table",
                "thresholds": [{"score": 0}],
            }
        )
        manifest["input_schema"]["properties"]["temp"] = {
            "type": "number",
            "title": "体温",
        }
        with self.assertRaises(ScaleSpecError):
            scale_form_spec(manifest)

    def test_clinician_scale_must_not_declare_patient_form(self):
        manifest = deepcopy(self.manifest)
        manifest["audience"] = "clinician"
        with self.assertRaises(ScaleSpecError):
            scale_form_spec(manifest)

    def test_input_schema_must_match_items(self):
        manifest = deepcopy(self.manifest)
        manifest["input_schema"]["properties"]["p9"] = {
            "type": "number",
            "title": "多出来的条目",
            "enum": [0, 1],
        }
        with self.assertRaises(ScaleSpecError):
            scale_form_spec(manifest)

    def test_output_schema_must_match_engine(self):
        manifest = deepcopy(self.manifest)
        manifest["output_schema"]["properties"]["extra"] = {"type": "string"}
        with self.assertRaises(ScaleSpecError):
            scale_form_spec(manifest)

    def test_missing_references_rejected(self):
        manifest = deepcopy(self.manifest)
        manifest["references"] = []
        with self.assertRaises(ScaleSpecError):
            scale_form_spec(manifest)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.registry = make_engine()
        self.state, _ = self.engine.create_session()

    def _draft(self, text=REQUEST):
        self.backend.commands.append(
            {
                "tool": "run_capability",
                "arguments": {"id": SCALE_ID, "input": {}, "evidence": text},
            }
        )
        return self.engine.submit(self.state.session_id, UserInput(text))

    def test_full_flow_scores_once_and_never_writes_a_fact(self):
        draft = self._draft()
        self.assertEqual(draft.kind, "calculation_input")
        self.assertEqual(self.state.calculations, [])
        answers = {item["id"]: 1 for item in draft.calculation_input["items"]}
        result = self.engine.submit(
            self.state.session_id,
            UserInput(
                "提交",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": answers,
                },
            ),
        )
        self.assertEqual(result.kind, "calculation_result")
        data = result.calculation_result["data"]
        self.assertEqual(data["value"], "8")
        self.assertEqual(data["band"]["label"], "轻度")
        self.assertEqual(data["result_renderer"], "scale/v1")
        self.assertEqual(len(data["items"]), 8)
        self.assertTrue(data["disclaimer"])
        self.assertEqual(self.state.facts, {})
        self.assertEqual(len(self.state.calculations), 1)

    def test_incomplete_form_returns_the_card_without_scoring(self):
        draft = self._draft()
        partial = {item["id"]: 1 for item in draft.calculation_input["items"][:3]}
        reply = self.engine.submit(
            self.state.session_id,
            UserInput(
                "提交",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": partial,
                },
            ),
        )
        self.assertEqual(reply.kind, "calculation_input")
        self.assertIn("未完成", reply.message + reply.calculation_input["feedback"])
        self.assertEqual(self.state.calculations, [])

    def test_answers_are_preserved_for_correction(self):
        draft = self._draft()
        partial = {item["id"]: 2 for item in draft.calculation_input["items"][:3]}
        reply = self.engine.submit(
            self.state.session_id,
            UserInput(
                "提交",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": partial,
                },
            ),
        )
        kept = reply.calculation_input["input"]
        self.assertEqual(kept, partial)

    def test_skip_leaves_no_calculation(self):
        draft = self._draft()
        self.engine.submit(
            self.state.session_id,
            UserInput(
                "先跳过",
                "skip_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": {},
                },
            ),
        )
        self.assertEqual(self.state.calculations, [])


if __name__ == "__main__":
    unittest.main()
