"""The location-answer scenario: a self-check task interrupted by a location
clarification, plus the confirmation payload shape."""
from medagent.consultation import UserInput
from tests.support.scripted import cmd


class LocationFlow:
    """A self-check flow interrupted by the symptom-location clarify."""

    def __init__(self):
        from tests.support.self_check_flow import SelfCheckFlow

        self.helper = SelfCheckFlow()
        self.engine = self.helper.engine
        self.state = self.helper.state
        self.backend = self.helper.backend

    def submit(self, text, choice=None, action=None):
        return self.helper.submit(text, choice, action)

    def offer(self, fields=None):
        """Offer the task, then interrupt it with the location clarify."""
        original = self.helper.offer()
        self.backend.commands = [
            cmd(
                "clarify",
                topic="symptom_location",
                message="头部哪里、哪一侧？不适是一点还是一片？",
                answer_fields=fields or ["region", "area", "side", "extent"],
            )
        ]
        reply = self.helper.submit("位置还不太确定")
        assert reply.task["interaction_id"] == original.task["interaction_id"]
        return reply

    @staticmethod
    def payload(reply):
        return {
            "schema": "location-answer/v1",
            "form_id": reply.task["answer_spec"]["id"],
            "answers": {
                "region": "head",
                "area": {"kind": "preset", "value": "temple"},
                "side": "right",
                "extent": "area",
            },
            "note": "",
        }

    def confirm(self, reply, payload):
        return self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对定位答案",
                "confirm_location_answer",
                reply.action_id,
                observation=payload,
            ),
        )
