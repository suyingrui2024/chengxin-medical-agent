"""Verify the failed synthetic dialogue against the current controller, offline."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from medagent.agent import AdaptiveConsultationEngine
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


class NoNetworkBackend:
    name = "offline-recorded-regression"

    def decide(self, *args):
        raise AssertionError("Recorded decisions exhausted; network is disabled")


def verify(root):
    directory = root / "docs/evaluations/2026-10-15/v0.6"
    failed_path = directory / "20261015-v06-dsh-deferred-goal-final.json"
    recovery_path = directory / "20261015-v06-dsh-recovery.json"
    failed, recovery = [
        json.loads(path.read_text()) for path in (failed_path, recovery_path)
    ]
    assert failed["synthetic_only"] is True and recovery["synthetic_only"] is True
    assert failed["source_sha256"] == recovery["source_sha256"]
    assert (
        recovery["decision_replay"]["sha256"]
        == hashlib.sha256(failed_path.read_bytes()).hexdigest()
    )
    outputs = (
        failed["cases"][0]["model_outputs"] + recovery["cases"][0]["model_outputs"]
    )
    spec = importlib.util.spec_from_file_location(
        "synthetic_evaluation", root / "scripts/evaluation/medical/evaluate_live.py"
    )
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    events, content = EventStore(), ContentRepository()
    backend = harness.ReplayThenLiveBackend(NoNetworkBackend(), events, outputs)
    engine = AdaptiveConsultationEngine(
        content=content,
        events=events,
        triage=TriageService(),
        self_checks=SelfCheckService(content.registry),
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    result = harness.full_interview(
        engine, events, exercise_correction=True, recover_once=True
    )
    result["checks"]["controller_summary_without_extra_decision"] = any(
        event.event_type == "agent.summary_ready" for event in events._events
    )
    result["checks"]["all_recorded_decisions_consumed"] = backend.position == len(
        outputs
    )
    result["passed"] = all(result["checks"].values())
    return {
        "kind": "offline-recorded-controller-regression/v1",
        "synthetic_only": True,
        "live_api_calls": 0,
        "recorded_decisions": backend.position,
        "prior_reports": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (failed_path, recovery_path)
        },
        "current_source_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "src/medagent").rglob("*"))
            if path.is_file()
            and path.suffix in {".py", ".js", ".json", ".txt", ".html", ".css", ".svg"}
        },
        "harness_sha256": hashlib.sha256(
            (root / "scripts/evaluation/medical/evaluate_live.py").read_bytes()
        ).hexdigest(),
        "result": result,
        "passed": result["passed"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(Path(__file__).resolve().parents[3])
    if args.output:
        if args.output.exists():
            raise SystemExit("Choose a new output path to preserve evidence")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "live_api_calls": 0,
                "recorded_decisions": report["recorded_decisions"],
                "checks": report["result"]["checks"],
            },
            ensure_ascii=False,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
