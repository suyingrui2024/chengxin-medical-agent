"""Replay the original short-evidence decisions without any network backend."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh output required")
    project = args.project.resolve()
    for name, expected in json.loads((HERE / "source-files.json").read_text()).items():
        assert (
            hashlib.sha256((project / name).read_bytes()).hexdigest() == expected
        ), name
    sys.path[:0] = [str(project / "src"), str(project)]
    from medagent.agent import AdaptiveConsultationEngine
    from medagent.consultation import UserInput
    from medagent.events import EventStore
    from medagent.knowledge import ContentRepository
    from medagent.reasoner import OrderedQuestionRanker
    from medagent.self_checks import SelfCheckService
    from medagent.triage import TriageService

    original = json.loads((HERE / "live-round1.json").read_text())["cases"][1]

    class SavedBackend:
        name = "saved-decisions-no-network"

        def __init__(self):
            self.commands = [
                json.loads(event["payload"]["content"])
                for event in original["events"]
                if event["event_type"] == "model.responded"
            ]
            self.used = 0

        def decide(self, session_id, messages):
            assert self.commands, "Recorded decisions exhausted; network disabled"
            self.used += 1
            return self.commands.pop(0)

    backend = SavedBackend()
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    state, _ = engine.create_session()
    reply = engine.submit(state.session_id, UserInput(original["synthetic_input"]))
    assert reply.kind == "summary"
    report = next(
        item for item in reply.summary["unknown_details"] if item["key"] == "allergies"
    )
    assert (
        report["evidence"]
        == original["state"]["facts"]["allergies"]["evidence"]
        == "是否药物过敏不确定"
    )
    assert report["context"]["text"] == original["synthetic_input"]
    assert "皮疹" in report["context"]["text"]
    assert state.facts["allergies"].value is None
    assert state.facts["allergies"].assertion.value == "unknown"
    assert backend.used == 3 and not backend.commands
    result = {
        "passed": True,
        "external_model_calls": 0,
        "saved_decisions_replayed": backend.used,
        "scope": "Counterfactual controller replay, not a new model trial.",
        "reply": reply.to_dict(),
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "reply"}))


if __name__ == "__main__":
    main()
