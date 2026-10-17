"""Replay both saved fault/recovery rounds with the frozen final controller."""

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
    assert not args.output.exists(), "Fresh output required"
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

    cases = []
    for name in ["live-round1.json", "live-round2.json"]:
        for original in json.loads((HERE / name).read_text())["cases"]:
            commands = []
            for event in original["events"]:
                if event["event_type"] == "evaluation.injected_decision":
                    commands.append(event["payload"]["proposal"])
                elif event["event_type"] == "model.responded":
                    commands.append(json.loads(event["payload"]["content"]))

            class SavedBackend:
                name = "saved-decisions-no-network"
                used = 0

                def decide(self, session_id, messages):
                    assert commands, "Saved decisions exhausted; no network fallback"
                    self.used += 1
                    return commands.pop(0)

            backend = SavedBackend()
            engine = AdaptiveConsultationEngine(
                content=ContentRepository(),
                triage=TriageService(),
                self_checks=SelfCheckService(),
                events=EventStore(),
                ranker=OrderedQuestionRanker(),
                backend=backend,
            )
            state, reply = engine.create_session()
            for step in original["steps"]:
                reply = engine.submit(
                    state.session_id,
                    UserInput(step["text"], step.get("choice_value"), reply.action_id),
                )
            assert reply.kind == "summary"
            assert not commands
            target = next(
                key
                for key in ["allergies", "medications_taken"]
                if original["state"]["facts"][key]["assertion"] == "unknown"
            )
            assert state.facts[target].assertion.value == "unknown"
            assert state.facts[target].value is None
            assert not any(
                item.key == target and item.assertion.value == "absent"
                for item in state.history
            )
            assert state.get("age_years") == 32
            cases.append(
                {
                    "round": name,
                    "id": original["id"],
                    "saved_proposals_replayed": backend.used,
                    "passed": True,
                }
            )
    assert len(cases) == 4
    result = {
        "passed": True,
        "external_model_calls": 0,
        "cases": cases,
        "scope": "Counterfactual replay of saved injected and actual decisions through the final frozen controller; not new model trials.",
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
