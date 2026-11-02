"""The deterministic-engine flow, driven turn by turn.

``ConsultationTestCase`` in ``tests/consultation/test_consultation.py`` used to
be both the test class and the fixture: two dialogue suites instantiated it to
borrow ``answer`` and ``establish_adult_complaint``.  The flow lives here now;
the test class composes it and so does everyone else.
"""
from medagent.consultation import UserInput
from medagent.server.runtime import build_runtime


class ConsultationFlow:
    """A started runtime plus the two helpers every turn-based test needs."""

    def __init__(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, self.reply = self.engine.create_session()

    def stop(self):
        self.host.stop()

    def answer(self, text, choice=None):
        self.reply = self.engine.submit(self.state.session_id, UserInput(text, choice))
        return self.reply

    def establish_adult_complaint(self, complaint="咳嗽发烧两天"):
        self.answer("我自己", "self")
        self.answer("男", "male")
        self.answer("32岁")
        return self.answer(complaint)
