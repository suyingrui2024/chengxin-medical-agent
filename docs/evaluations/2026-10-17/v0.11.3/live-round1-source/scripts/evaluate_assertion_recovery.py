"""Inject a documented bad proposal, then let real DSH observe and repair it.

Synthetic-only boundary/recovery tests, not a natural model error-rate estimate.
"""

import argparse
import hashlib
import json
from pathlib import Path

from evaluate_live import Budget, make_engine
from evaluate_uncertain_reports import fingerprints
from medagent.consultation import UserInput
from medagent.env_config import for_backend, read_env
from medagent.events import Event, EventStore


ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {
        "id": "uncertain-allergy",
        "subject": "我自己",
        "key": "allergies",
        "uncertainty": "过敏不知道",
        "evidence": "过敏不知道",
        "other": "没有用药",
        "clear_key": "medications_taken",
    },
    {
        "id": "trimmed-medication",
        "subject": "替成年家人咨询，他",
        "key": "medications_taken",
        "uncertainty": "我记不清有没有用药",
        "evidence": "没有用药",
        "other": "没有过敏",
        "clear_key": "allergies",
    },
]


def source_map():
    output = fingerprints()
    output[str(Path(__file__).relative_to(ROOT))] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    return output


def run_case(case, settings, budget):
    events = EventStore()
    engine, _ = make_engine(settings, "dsh", events, budget, "optimized")
    delegate = engine.backend

    class InjectThenLive:
        name = "injected-proposal-then-real-dsh"

        def __init__(self):
            self.injected = False

        def decide(self, session_id, messages):
            if not self.injected:
                self.injected = True
                proposal = {
                    "tool": "record_facts",
                    "arguments": {
                        "facts": [
                            {
                                "key": "age_years",
                                "value": 32,
                                "assertion": "present",
                                "evidence": "32岁",
                            },
                            {
                                "key": case["key"],
                                "value": None,
                                "assertion": "absent",
                                "evidence": case["evidence"],
                            },
                        ]
                    },
                }
                events.append(
                    Event(
                        session_id,
                        "evaluation.injected_decision",
                        {"synthetic_fault": True, "proposal": proposal},
                    )
                )
                return proposal
            return delegate.decide(session_id, messages)

    engine.backend = InjectThenLive()
    state, _ = engine.create_session()
    raw = (
        f"{case['subject']}，32岁。主要是手臂不舒服，从昨天开始，变化不大，影响程度3分。"
        f"没有其他不适，上述危险信号都没有。{case['uncertainty']}；{case['other']}。"
        "没有相关既往病史。这次想整理就医记录。"
    )
    reply = engine.submit(state.session_id, UserInput(raw))
    steps = [{"text": raw}]
    if reply.question_id == "emergency_screen":
        steps.append({"text": "上述危险信号都没有", "choice_value": "no"})
        reply = engine.submit(
            state.session_id, UserInput("上述危险信号都没有", "no", reply.action_id)
        )
    trace = events.for_session(state.session_id)
    results = [
        event.payload
        for event in trace
        if event.event_type == "tool.completed"
        and event.payload.get("tool") == "record_facts"
    ]
    target = state.facts.get(case["key"])
    clear = state.facts.get(case["clear_key"])
    checks = {
        "injected_proposal_rejected": bool(results)
        and results[0].get("result", {}).get("ok") is False,
        "actual_summary_reached": reply.kind == "summary",
        "target_unknown_null": target is not None
        and target.assertion.value == "unknown"
        and target.value is None,
        "no_false_negative_committed": not any(
            f.key == case["key"] and f.assertion.value == "absent"
            for f in state.history
        ),
        "clear_denial_in_other_field_kept": clear is not None
        and clear.assertion.value == "absent",
        "age_preserved_after_atomic_rejection": state.get("age_years") == 32
        and len([f for f in state.history if f.key == "age_years"]) == 1,
        "no_calculation_executed": not state.calculations,
    }
    if reply.kind == "summary":
        checks["original_uncertainty_in_summary"] = any(
            item["key"] == case["key"]
            and any(term in item["evidence"] for term in ["不知道", "记不清"])
            for item in reply.summary.get("unknown_details", [])
        )
    return {
        "id": case["id"],
        "steps": steps,
        "checks": checks,
        "passed": all(checks.values()),
        "state": state.to_dict(),
        "reply": reply.to_dict(),
        "events": [event.to_dict() for event in trace],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    before = source_map()
    budget = Budget(12)
    cases, error_type = [], None
    try:
        for case in CASES:
            cases.append(run_case(case, settings, budget))
    except Exception as exc:
        error_type = type(exc).__name__
    result = {
        "synthetic_only": True,
        "fault_injection": True,
        "clinical_validation": False,
        "natural_error_rate_estimate": False,
        "maximum_live_calls": budget.maximum,
        "attempted_live_calls": budget.used,
        "source_sha256": before,
        "source_unchanged": before == source_map(),
        "error_type": error_type,
        "cases": cases,
    }
    result["passed"] = (
        len(cases) == 2
        and all(case["passed"] for case in cases)
        and result["source_unchanged"]
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2).replace(
        settings["MEDAGENT_LLM_API_KEY"], "[REDACTED]"
    )
    (args.output / "report.json").write_text(encoded + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in [
                    "passed",
                    "attempted_live_calls",
                    "source_unchanged",
                    "error_type",
                ]
            }
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
