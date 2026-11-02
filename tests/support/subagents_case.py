"""The subagent coordinator scenario: a seeded case state and a scripted
backend that replays per-session views.  Moved out of
``tests/subagents/test_subagents.py``, which two other suites were importing
as a fixture."""
import json

from medagent.core.domain import Assertion, CaseState, Fact, Source
from medagent.clinical.reasoning import Hypothesis, ReasoningWorkspace
from medagent.subagents import SubagentCoordinator



CHIEF_EVIDENCE = "我咳嗽三天了，还有点嗓子疼"



COUGH_EVIDENCE = "主要是干咳，没有痰"



def make_state(session_id="case-1"):
    state = CaseState(session_id=session_id)
    state.record(
        Fact(
            key="chief_complaint",
            value="咳嗽三天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=CHIEF_EVIDENCE,
        )
    )
    state.record(
        Fact(
            key="resp_cough_details",
            value="干咳",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=COUGH_EVIDENCE,
        )
    )
    state.record(
        Fact(
            key="medications_taken",
            value="吃过感冒药",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence="吃过一片感冒药",
        )
    )
    workspace = ReasoningWorkspace(version=1)
    workspace.hypotheses["uri"] = Hypothesis(
        id="uri",
        label="上呼吸道感染",
        category="common_condition",
        prior_band="high",
    )
    state.reasoning = workspace.to_dict()
    state.transcript = [
        {"role": "user", "content": CHIEF_EVIDENCE},
        {"role": "assistant", "content": "咳嗽是干咳还是有痰？"},
        {"role": "user", "content": COUGH_EVIDENCE},
    ]
    return state



def report(findings=None, conclusion="小结", level="low", **extra):
    arguments = {
        "conclusion": conclusion,
        "findings": findings or [],
        "uncertainty": {"level": level, "reasons": []},
    }
    arguments.update(extra)
    return {"tool": "subagent_report", "arguments": arguments}



class StubBackend:
    """Scripts sub-model replies through a handler function."""

    name = "stub"

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.views = {}

    def decide_cancellable(self, session_id, messages, cancelled):
        self.calls.append(session_id)
        if session_id not in self.views:
            self.views[session_id] = json.loads(messages[1]["content"])
        return self.handler(session_id, messages, cancelled)



def coordinator(backend, events, **kwargs):
    kwargs.setdefault("mode", "advisory")
    return SubagentCoordinator(events, backend, **kwargs)



def spawn_proposal(kind="evidence_lookup", scope=None, **branch_extra):
    branch = {"branch_id": "b1", "objective": "核对证据", "scope": scope or {}}
    branch.update(branch_extra)
    return {"task_kind": kind, "objective": "测试", "branches": [branch]}

