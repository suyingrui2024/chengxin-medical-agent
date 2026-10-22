"""Bounded real-provider evaluation of synthetic patient conversion and resumption."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import threading

from medagent.agent import AdaptiveConsultationEngine
from medagent.backends import DshBackend
from medagent.consultation import UserInput
from medagent.env_config import for_backend, read_env
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from scripts.evaluation.medical.test_live_desktop import EvaluationGateway


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh output required")
    root = Path(__file__).resolve().parents[3]
    frozen = json.loads(args.source_manifest.read_text())

    def unchanged():
        return all(
            (root / n).is_file()
            and hashlib.sha256((root / n).read_bytes()).hexdigest() == d
            for n, d in frozen.items()
        )

    assert unchanged(), "Source differs from frozen snapshot"
    spent = 0
    for path in args.output.parent.glob("*.json"):
        try:
            previous = json.loads(path.read_text())
            if previous.get("evaluation") == "glucose-calculations/v1":
                spent += max(0, int(previous.get("attempted_calls", 0)))
        except (ValueError, OSError):
            pass
    maximum = min(8, 16 - spent)
    if maximum <= 0:
        raise SystemExit("Cumulative 16-call evaluation limit reached")
    settings = for_backend(read_env(root / ".env"), "dsh")
    token = secrets.token_hex(32)
    gateway = EvaluationGateway(settings, maximum, token)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_API_KEY": token,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
    }
    cases = []
    try:
        for label, text, supplied, expected, initial in [
            (
                "explicit_reading",
                "请把血糖180mg/dL换成mmol/L，只做单位换算。",
                {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"},
                "10",
                {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"},
            ),
            (
                "missing_parameters",
                "想做血糖单位换算，但还没有给数字和单位，请先让我填写。",
                {"value": "10", "from_unit": "mmol/L", "to_unit": "mg/dL"},
                "180",
                {},
            ),
        ]:
            events, checks = EventStore(), {}
            engine = AdaptiveConsultationEngine(
                content=ContentRepository(),
                triage=TriageService(),
                self_checks=SelfCheckService(),
                events=events,
                ranker=OrderedQuestionRanker(),
                backend=DshBackend(events, root, config),
            )
            state, opening = engine.create_session()
            failure = None
            try:
                reply = engine.submit(state.session_id, UserInput(text))
                checks["registered_glucose_form"] = (
                    reply.kind == "calculation_input"
                    and reply.calculation_input["capability_id"] == "calculate.glucose"
                )
                assert checks["registered_glucose_form"]
                checks["actual_source_and_prefill"] = (
                    reply.calculation_input["input"] == initial
                    and reply.calculation_input["source_quote"] in text
                )
                assert checks["actual_source_and_prefill"]
                before_facts, count = (
                    state.to_dict()["facts"],
                    gateway.metrics()["attempted_calls"],
                )
                reply = engine.submit(
                    state.session_id,
                    UserInput(
                        "核对并计算",
                        "confirm_calculation",
                        reply.action_id,
                        {
                            "proposal_id": reply.calculation_input["proposal_id"],
                            "input": supplied,
                        },
                    ),
                )
                result = reply.calculation_result
                checks["host_result_with_formula_and_sources"] = (
                    reply.kind == "calculation_result"
                    and result["data"]["value"] == expected
                    and result["data"]["approximate"] is True
                    and result["references"][0]["url"]
                    == "https://diabetes.org/about-diabetes/common-terms"
                )
                checks["confirmed_but_measurement_not_validated"] = (
                    result["input_status"] == "confirmed"
                    and result["quality"] == "not_assessed"
                )
                checks["calculation_preserves_case_facts"] = (
                    state.to_dict()["facts"] == before_facts
                )
                reply = engine.submit(
                    state.session_id,
                    UserInput(
                        "继续问诊",
                        "resume_after_calculation",
                        reply.action_id,
                        {"execution_id": result["execution_id"]},
                    ),
                )
                checks["returns_to_original_question"] = (
                    reply.question_id == opening.question_id
                    and reply.message == opening.message
                )
                checks["confirmation_and_resumption_use_zero_extra_model_calls"] = (
                    gateway.metrics()["attempted_calls"] == count
                )
                assert all(checks.values())
            except Exception as exc:
                failure = type(exc).__name__
            cases.append(
                {
                    "label": label,
                    "checks": checks,
                    "failure_type": failure,
                    "state": state.to_dict(),
                    "events": [
                        e.to_dict() for e in events.for_session(state.session_id)
                    ],
                    "passed": failure is None and all(checks.values()),
                }
            )
    finally:
        gateway.shutdown()
        gateway.server_close()
        unchanged_after = unchanged()
        report = {
            "evaluation": "glucose-calculations/v1",
            "synthetic_only": True,
            "scope": "Two fresh synthetic patient requests using the official DSH and real provider; controlled confirmation and resumption. No end-to-end clinical diagnosis, visual model or general research-workbench evaluation.",
            "cases": cases,
            "frozen_sources_unchanged": unchanged_after,
            **gateway.metrics(),
            "passed": len(cases) == 2
            and all(c["passed"] for c in cases)
            and unchanged_after,
        }
        encoded = (
            json.dumps(report, ensure_ascii=False, indent=2)
            .replace(token, "[evaluation-token-redacted]")
            .replace(settings["MEDAGENT_LLM_API_KEY"], "[provider-key-redacted]")
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n")
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "attempted_calls": report["attempted_calls"],
                    "cases": [
                        {
                            "label": c["label"],
                            "checks": c["checks"],
                            "failure_type": c["failure_type"],
                        }
                        for c in cases
                    ],
                },
                ensure_ascii=False,
            )
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
