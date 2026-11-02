"""The self-check scenario: an offered task with its opening facts recorded.

Built on :func:`support.calculation_case.calculation_runtime` with a fresh
backend swapped in, exactly as ``SelfCheckConversationTests.setUp`` used to
assemble it from two borrowed test objects.
"""
from medagent.consultation import UserInput
from tests.support.calculation_case import calculation_runtime
from tests.support.scripted import ScriptBackend, cmd, fact


class SelfCheckFlow:
    """A calculation-capable engine offered a self-check task."""

    def __init__(self):
        engine, _backend, state, _opening = calculation_runtime()
        self.engine = engine
        self.state = state
        self.backend = self.engine.backend = ScriptBackend()

    def submit(self, text, choice=None, action=None):
        return self.engine.submit(
            self.state.session_id, UserInput(text, choice, action)
        )

    def offer(self, task="body_location"):
        """Record the opening facts and accept the offered self-check task."""
        chief = "头疼" if task == "body_location" else "发热"
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("age_years", 32, "32岁"),
                    fact("chief_complaint", chief, chief),
                    fact("emergency_screen", None, "上述危险信号都没有", "absent"),
                ],
            ),
            cmd("show_self_check", task_id=task),
        ]
        return self.submit(f"我自己32岁，{chief}；上述危险信号都没有。")

    def medicine(self):
        return cmd(
            "record_facts",
            facts=[fact("medications_taken", "昨晚用过药物甲", "昨晚用过药物甲")],
        )
