"""Bounded real-model checks of synthetic mixed-uncertainty patient reports.

The controller must actually reach its summary; the evaluator never calls the
summary builder to bypass unfinished questions. This is not clinical validation.
"""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
import hashlib
import json
from pathlib import Path

from scripts.evaluation.medical.evaluate_live import Budget, make_engine
from medagent.consultation import UserInput
from medagent.env_config import for_backend, read_env
from medagent.events import EventStore


ROOT = Path(__file__).resolve().parents[3]
REPORTS = {
    "medications_taken": ["药物甲", "用量不清楚"],
    "allergies": ["皮疹", "是否药物过敏不确定"],
}
TEXT = (
    "{subject}，32岁。主要是手臂不舒服，从昨天开始，变化不大，影响程度3分，"
    "没有其他不适，没有上述危险信号。昨晚用过药物甲，用量不清楚。"
    "以前有过皮疹，是否药物过敏不确定。没有相关既往病史。这次想整理就医记录。"
)


def fingerprints():
    paths = [ROOT / "package.json", ROOT / "package-lock.json", Path(__file__)]
    paths += [
        path
        for path in (ROOT / "src/medagent").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    paths += [path for path in (ROOT / "integrations/dsh").glob("*") if path.is_file()]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(paths))
    }


def run_case(settings, mode, budget, subject):
    events = EventStore()
    engine, _delegate = make_engine(settings, mode, events, budget, "optimized")
    state, _ = engine.create_session()
    raw = TEXT.format(subject=subject)
    # Submit general history naturally and answer an actual screening question
    # if asked. Do not call internal summary methods to force completion.
    reply = engine.submit(state.session_id, UserInput(raw))
    steps = [{"text": raw}]
    if reply.question_id == "emergency_screen":
        steps.append({"text": "上述危险信号都没有", "choice_value": "no"})
        reply = engine.submit(
            state.session_id, UserInput("上述危险信号都没有", "no", reply.action_id)
        )
    summary = reply.summary if reply.kind == "summary" else None
    checks = {"actual_summary_reached": summary is not None}
    for key, markers in REPORTS.items():
        current = state.facts.get(key)
        shown = []
        if summary:
            shown += [
                str(item["value"]) for item in summary["known"] if item["key"] == key
            ]
            shown += [
                item["evidence"]
                for item in summary.get("unknown_details", [])
                if item["key"] == key
            ]
            shown += [
                item["context"]["text"]
                for item in summary.get("unknown_details", [])
                if item["key"] == key
                and item.get("context", {}).get("scope") == "source_turn"
            ]
        checks[key + "_report_and_uncertainty_visible"] = bool(shown) and all(
            any(marker in value for value in shown) for marker in markers
        )
        checks[key + "_unknown_not_promoted"] = current is not None and (
            current.assertion.value != "unknown" or current.value is None
        )
    checks["no_calculation_executed"] = not state.calculations
    return {
        "subject": subject,
        "synthetic_input": raw,
        "steps": steps,
        "checks": checks,
        "passed": all(checks.values()),
        "state": state.to_dict(),
        "reply": reply.to_dict(),
        "events": [event.to_dict() for event in events.for_session(state.session_id)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--backend", choices=["compatible", "dsh"], default="dsh")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    settings = for_backend(read_env(ROOT / ".env"), args.backend)
    budget = Budget(12)
    before = fingerprints()
    cases = []
    error = None
    try:
        for subject in ["我自己", "给我的成年家人咨询，他"]:
            cases.append(run_case(settings, args.backend, budget, subject))
    except Exception as exc:
        # Do not serialize transport exceptions that might include credentials.
        error = type(exc).__name__
    result = {
        "synthetic_only": True,
        "clinical_validation": False,
        "backend": args.backend,
        "maximum_calls": budget.maximum,
        "attempted_calls": budget.used,
        "source_sha256": before,
        "source_unchanged": before == fingerprints(),
        "error_type": error,
        "cases": cases,
    }
    result["passed"] = (
        len(cases) == 2
        and all(case["passed"] for case in cases)
        and result["source_unchanged"]
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    encoded = encoded.replace(settings["MEDAGENT_LLM_API_KEY"], "[REDACTED]")
    (args.output / "report.json").write_text(encoded + "\n")
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "attempted_calls": budget.used,
                "completed_cases": len(cases),
                "error_type": error,
            }
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
