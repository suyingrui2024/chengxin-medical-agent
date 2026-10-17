from copy import deepcopy
import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.domain import Assertion, Phase, Source, ObservationQuality
from medagent.events import EventStore
from medagent.extensions.builtin import load_registry, convert_units, observation
from medagent.interpreter import Interpretation
from medagent.extensions.registry import Capability, CapabilityRegistry
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from medagent.workspace import Workspace, StaleCompositionError


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def test_decimal_conversion_is_exact_and_has_provenance(self):
        result = self.registry.execute(
            "calculate.units",
            {"value": "0.000001", "from_unit": "kg", "to_unit": "mg"},
            "developer_preview",
        )
        self.assertEqual(result["data"]["value"], "1")
        self.assertIn("0.000001 × 1000 ÷ 0.001 = 1 mg", result["data"]["formula"])
        self.assertEqual(result["input_status"], "unconfirmed")
        self.assertEqual(result["quality"], "not_assessed")
        self.assertEqual(result["registry_sha256"], self.registry.revision)

    def test_dimension_mismatch_and_invalid_input_are_rejected(self):
        for arguments in [
            {"value": "1", "from_unit": "g", "to_unit": "mL"},
            {"value": "NaN", "from_unit": "g", "to_unit": "kg"},
            {"value": "-1", "from_unit": "g", "to_unit": "kg"},
            {"value": "1e100", "from_unit": "g", "to_unit": "kg"},
            {"value": 1, "from_unit": "g", "to_unit": "kg"},
            {"value": "1", "from_unit": "g", "to_unit": "kg", "shell": "id"},
        ]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.registry.execute("calculate.units", arguments, "test")

    def test_manifest_copies_cannot_change_live_registry(self):
        catalog = self.registry.catalog()
        catalog["capabilities"][0]["version"] = "99.0.0"
        self.assertNotEqual(
            self.registry.catalog()["capabilities"][0]["version"], "99.0.0"
        )

    def test_disabled_calculation_and_content_cannot_execute(self):
        registry = self.registry.with_enabled("calculate.units", False).with_enabled(
            "observe.temperature", False
        )
        with self.assertRaises(ValueError):
            registry.execute("calculate.units", {}, "test")
        with self.assertRaises(KeyError):
            registry.interpret_task("axillary_temperature", "38.2", None)
        self.assertIn("axillary_temperature", self.registry.tasks)

    def test_content_package_preserves_unknown_and_patient_quality(self):
        checks = SelfCheckService(self.registry)
        skipped = checks.interpret("axillary_temperature", "跳过", "skip")
        self.assertEqual(skipped.interpretation.assertion, Assertion.UNKNOWN)
        self.assertEqual(skipped.interpretation.quality.value, "not_done")
        completed = checks.interpret("axillary_temperature", "38.2℃")
        self.assertEqual(completed.interpretation.value, 38.2)
        self.assertEqual(completed.interpretation.source.value, "patient")
        self.assertEqual(completed.interpretation.quality.value, "not_assessed")

    def test_contract_rejects_incompatible_api_and_ungranted_permissions(self):
        manifest = next(
            item
            for item in self.registry.catalog()["capabilities"]
            if item["id"] == "calculate.units"
        )
        for field, value in [
            ("api_version", "unknown/v100"),
            ("permissions", ["network"]),
        ]:
            altered = {**manifest, field: value}
            with self.assertRaises(ValueError):
                CapabilityRegistry(
                    [Capability(altered, execute=convert_units)],
                    {"enabled": [altered["id"]]},
                )

    def test_new_registered_capability_uses_existing_agent_dispatch(self):
        manifest = next(
            item
            for item in self.registry.catalog()["capabilities"]
            if item["id"] == "calculate.units"
        )
        manifest["id"] = "calculate.fixture"
        calls = []

        def implementation(arguments):
            calls.append(arguments)
            return convert_units(arguments)

        registry = CapabilityRegistry(
            [Capability(manifest, execute=implementation)],
            {"id": "fixture", "version": "0.1.0", "enabled": [manifest["id"]]},
        )

        class Backend:
            name = "fixture"

            def __init__(self):
                self.commands = [
                    {
                        "tool": "run_capability",
                        "arguments": {
                            "id": "calculate.fixture",
                            "input": {
                                "value": "1.25",
                                "from_unit": "kg",
                                "to_unit": "g",
                            },
                            "evidence": "请换算1.25kg为g",
                        },
                    },
                    {"tool": "ask_question", "arguments": {"question_id": "age"}},
                ]
                self.messages = []

            def decide(self, sid, messages):
                self.messages = deepcopy(messages)
                return self.commands.pop(0)

        backend = Backend()
        events = EventStore()
        engine = AdaptiveConsultationEngine(
            content=ContentRepository(registry),
            triage=TriageService(),
            self_checks=SelfCheckService(registry),
            events=events,
            ranker=OrderedQuestionRanker(),
            backend=backend,
        )
        state, opening = engine.create_session()
        reply = engine.submit(state.session_id, UserInput("请换算1.25kg为g"))
        self.assertEqual(reply.kind, "calculation_input")
        self.assertEqual(
            len(calls),
            0,
            "a model proposal must not execute before patient confirmation",
        )
        self.assertEqual(state.calculations, [])
        reply = engine.submit(
            state.session_id,
            UserInput(
                "核对换算：1.25 kg → g",
                "confirm_calculation",
                reply.action_id,
                {
                    "proposal_id": reply.calculation_input["proposal_id"],
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                },
            ),
        )
        self.assertEqual(reply.kind, "calculation_result")
        self.assertEqual(len(calls), 1)
        self.assertEqual(state.calculations[0]["data"]["value"], "1250")
        self.assertEqual(state.facts, {}, "calculation must not become a clinical fact")
        self.assertEqual(state.calculations[0]["input_status"], "confirmed")
        resumed = engine.submit(
            state.session_id,
            UserInput(
                "继续问诊",
                "resume_after_calculation",
                reply.action_id,
                {"execution_id": reply.calculation_result["execution_id"]},
            ),
        )
        self.assertEqual(resumed.message, opening.message)
        self.assertEqual(
            len(backend.commands),
            1,
            "confirmation and resume do not need a model decision",
        )
        self.assertTrue(
            any(
                event.event_type == "capability.completed"
                for event in events.for_session(state.session_id)
            )
        )
        state.phase = Phase.CLOSED
        with self.assertRaises(ValueError):
            engine.run_capability(
                state, "calculate.fixture", calls[0], "test", "closed-turn"
            )

    def test_malformed_output_is_rejected_before_a_case_can_use_it(self):
        manifest = next(
            item
            for item in self.registry.catalog()["capabilities"]
            if item["id"] == "calculate.units"
        )
        registry = CapabilityRegistry(
            [Capability(manifest, execute=lambda _: {"value": "1250"})],
            {"enabled": [manifest["id"]]},
        )
        with self.assertRaises(ValueError):
            registry.execute(
                manifest["id"],
                {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                "test",
            )

    def test_observation_dependency_contract_rejects_invalid_fields(self):
        for invalidators in (
            "chief_complaint",
            ["unknown_field"],
            ["age_years", "age_years"],
            [None],
        ):
            manifest = next(
                item
                for item in self.registry.catalog()["capabilities"]
                if item["id"] == "observe.location"
            )
            manifest["task"]["invalidate_on"] = invalidators
            with self.subTest(value=invalidators), self.assertRaises(ValueError):
                CapabilityRegistry(
                    [Capability(manifest, interpret=lambda text, choice: None)],
                    {"enabled": [manifest["id"]]},
                )

    def observation_engine(self, handler):
        manifest = next(
            item
            for item in self.registry.catalog()["capabilities"]
            if item["id"] == "observe.location"
        )
        manifest["id"] = "observe.fixture"
        manifest["task"]["id"] = "fixture_location"
        manifest["task"]["result_key"] = "fixture_location"
        manifest["task"]["result_label"] = "示例位置"
        registry = CapabilityRegistry(
            [Capability(manifest, interpret=handler)],
            {"id": "fixture", "version": "0.1.0", "enabled": [manifest["id"]]},
        )
        host = build_runtime(backend="demo", registry=registry)
        self.addCleanup(host.stop)
        engine = host.start().services.require("consultation")
        state, _ = engine.create_session()
        engine._record_fact(
            state, "chief_complaint", Interpretation("头痛", Assertion.PRESENT), "fixture"
        )
        engine._record_fact(
            state, "emergency_screen", Interpretation(None, Assertion.ABSENT), "fixture"
        )
        engine._offer_task(state, "头痛", "fixture_location")
        return engine, state

    def test_new_content_pack_uses_existing_flow_and_invalidation(self):
        engine, state = self.observation_engine(
            lambda text, choice: observation(text, choice, "location")
        )
        engine.submit(state.session_id, UserInput("头部", "head"))
        self.assertEqual(state.get("fixture_location"), "头/面部")
        summary = engine._finish(state).summary
        self.assertEqual(
            next(
                item for item in summary["known"] if item["key"] == "fixture_location"
            )["label"],
            "示例位置",
        )
        engine.correct_fact(state.session_id, "chief_complaint", "腹部痛")
        self.assertNotIn("fixture_location", state.facts)
        self.assertTrue(any(fact.key == "fixture_location" for fact in state.history))

    def test_invalid_observation_keeps_task_pending_and_skip_still_works(self):
        engine, state = self.observation_engine(
            lambda text, choice: Interpretation(
                "正常", Assertion.PRESENT, Source.DEVICE, ObservationQuality.ADEQUATE
            )
        )
        reply = engine.submit(state.session_id, UserInput("已经做了"))
        self.assertEqual(reply.kind, "self_check")
        self.assertEqual(state.active_task, "fixture_location")
        self.assertNotIn("fixture_location", state.facts)
        engine.submit(state.session_id, UserInput("跳过", "skip"))
        self.assertEqual(state.facts["fixture_location"].assertion, Assertion.UNKNOWN)
        self.assertEqual(
            state.facts["fixture_location"].quality, ObservationQuality.NOT_DONE
        )


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime(backend="demo")
        context = self.host.start()
        self.workspace = Workspace(
            context.services.require("consultation"),
            context.services.require("events"),
            "demo",
        )

    def tearDown(self):
        self.workspace.close()
        self.host.stop()

    def test_reconfiguration_keeps_old_session_and_its_active_task(self):
        engine = self.workspace.engine
        state, _ = engine.create_session()
        state.active_task = "axillary_temperature"
        snapshot = deepcopy(state.runtime_snapshot)
        revision = self.workspace.catalog()["revision"]
        new = self.workspace.reconfigure(
            {
                "id": "observe.temperature",
                "enabled": False,
                "expected_revision": revision,
            }
        )
        self.assertNotEqual(new["revision"], revision)
        self.assertIs(self.workspace.engine_for(state.session_id), engine)
        self.assertEqual(snapshot, state.runtime_snapshot)
        self.assertEqual(
            engine.self_checks.interpret(
                state.active_task, "38.2"
            ).interpretation.value,
            38.2,
        )
        new_state, _ = self.workspace.engine.create_session()
        self.assertNotIn("axillary_temperature", self.workspace.engine.content.tasks)
        self.assertNotEqual(new_state.runtime_snapshot, snapshot)

    def test_stale_or_invalid_update_cannot_change_active_composition(self):
        before = self.workspace.engine
        for payload, error in [
            (
                {
                    "id": "observe.temperature",
                    "enabled": False,
                    "expected_revision": "stale",
                },
                StaleCompositionError,
            ),
            (
                {
                    "id": "missing",
                    "enabled": False,
                    "expected_revision": self.workspace.catalog()["revision"],
                },
                ValueError,
            ),
        ]:
            with self.assertRaises(error):
                self.workspace.reconfigure(payload)
            self.assertIs(self.workspace.engine, before)

    def test_template_switch_and_preview_are_case_independent(self):
        revision = self.workspace.catalog()["revision"]
        result = self.workspace.preview(
            "calculate.units",
            {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
            revision,
        )
        self.assertEqual(result["data"]["value"], "1250")
        self.assertEqual(self.workspace.sessions(), [])
        catalog = self.workspace.reconfigure(
            {"template_id": "history", "expected_revision": revision}
        )
        self.assertFalse(any(item["enabled"] for item in catalog["capabilities"]))
        self.assertEqual(self.workspace.engine.content.tasks, {})


if __name__ == "__main__":
    unittest.main()
