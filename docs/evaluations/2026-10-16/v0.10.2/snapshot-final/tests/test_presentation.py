from copy import deepcopy
import json
import unittest

from medagent.domain import AgentReply, Phase
from medagent.extensions.builtin import load_registry
from medagent.extensions.registry import Capability, CapabilityRegistry
from medagent.presentation import UI_VERSION, calculation_presentation
from medagent.knowledge import ContentRepository


class PresentationTests(unittest.TestCase):
    def test_visual_question_uses_actual_choices_and_preserves_custom_clarification(
        self,
    ):
        question = ContentRepository().question("emergency_screen")
        reply = AgentReply(
            question.prompt,
            question.phase,
            question_id=question.id,
            quick_replies=question.options,
        )
        view = reply.to_dict()["presentation"]["blocks"][-1]
        self.assertEqual(view["type"], "question")
        self.assertTrue(view["data"]["canonical"])
        self.assertEqual(view["data"]["choices"][2]["value"], "unknown")
        reply.message = "请确认你刚才说的变化，是加重还是减轻？"
        custom = reply.to_dict()["presentation"]["blocks"][-1]["data"]
        self.assertFalse(custom["canonical"])
        self.assertEqual(custom["prompt"], reply.message)

    def test_invalid_observation_has_visible_structured_feedback(self):
        task = ContentRepository().tasks["axillary_temperature"]
        reply = AgentReply(
            "无法记录，请确认单位。",
            Phase.OBSERVATION,
            kind="self_check",
            task=task,
            input_error=True,
        )
        data = reply.to_dict()["presentation"]["blocks"][-1]["data"]
        self.assertEqual(data["feedback"], reply.message)
        self.assertNotIn("feedback", task)

    def test_patient_projection_omits_developer_trace_and_preserves_plain_text(self):
        reply = AgentReply(
            '<img src=x onerror="alert(1)">',
            Phase.HISTORY,
            developer_trace={"reasoning": "private-debug-content"},
        )
        view = reply.to_dict()["presentation"]
        self.assertEqual(view["schema_version"], UI_VERSION)
        self.assertEqual(view["blocks"][0]["data"]["text"], reply.message)
        self.assertNotIn("private-debug-content", json.dumps(view))

    def test_actual_calculation_retains_input_quality_and_execution_identity(self):
        result = load_registry().execute(
            "calculate.units",
            {
                "value": "1.25",
                "from_unit": "kg",
                "to_unit": "g",
            },
            "developer_preview",
        )
        data = result["presentation"]["blocks"][0]["data"]
        self.assertEqual(data["value"], "1250")
        self.assertEqual(data["input_status"], "unconfirmed")
        self.assertEqual(data["quality"], "not_assessed")
        self.assertEqual(data["inputs"][0]["value"], "1.25")
        result["execution_id"] = "test-execution"
        self.assertTrue(
            calculation_presentation(result)["blocks"][0]["id"].endswith(
                ":test-execution"
            )
        )
        data["inputs"][0]["value"] = "changed-in-view"
        self.assertEqual(result["input"]["value"], "1.25")

    def test_emergency_uses_notice_without_requiring_an_ordinary_summary(self):
        reply = AgentReply(
            "优先求助",
            Phase.CLOSED,
            kind="emergency",
            summary={
                "care_route": "立即急救",
                "triggers": ["已有规则命中"],
            },
        )
        blocks = reply.to_dict()["presentation"]["blocks"]
        self.assertEqual(blocks[-1]["type"], "notice")
        self.assertEqual(blocks[-1]["data"]["title"], "立即急救")

    def test_step_assets_cannot_load_external_content_or_mismatch_steps(self):
        registry = load_registry()
        entry = registry._entries["observe.temperature"]
        for assets in [
            ["https://example.org/image.svg"] * 3,
            ["/temperature.svg#step-1"],
        ]:
            with self.subTest(assets=assets):
                manifest = deepcopy(entry.manifest)
                manifest["task"]["step_assets"] = assets
                with self.assertRaises(ValueError):
                    CapabilityRegistry(
                        [Capability(manifest, interpret=entry.interpret)],
                        {
                            "id": "test",
                            "version": "1.0.0",
                            "enabled": [manifest["id"]],
                        },
                    )


if __name__ == "__main__":
    unittest.main()
