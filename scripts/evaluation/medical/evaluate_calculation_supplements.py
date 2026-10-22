"""Bounded real DSH evaluation of calculation-first mixed synthetic patient turns."""

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
from medagent.domain import Assertion
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

    assert unchanged()
    spent = 0
    for path in args.output.parent.glob("*.json"):
        try:
            previous = json.loads(path.read_text())
            if previous.get("evaluation") == "calculation-supplements/v1":
                spent += max(0, int(previous.get("attempted_calls", 0)))
        except (OSError, ValueError):
            pass
    maximum = min(10, 20 - spent)
    if maximum <= 0:
        raise SystemExit("Cumulative 20-call limit reached")
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
        for label, text, identity, values, expected, field, tokens, inject in [
            (
                "medication_with_retry",
                "我自己32岁；请先把1.25kg换算成g；另外，昨晚用过药物甲，具体用量不清楚。先让我核对换算，之后继续处理补充。",
                "calculate.units",
                {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                "1250",
                "medications_taken",
                ["药物甲"],
                True,
            ),
            (
                "allergy_with_glucose",
                "请先把血糖180mg/dL换成mmol/L；补充：我对药物甲过敏，出现过皮疹。先核对换算，之后再处理补充。",
                "calculate.glucose",
                {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"},
                "10",
                "allergies",
                ["药物甲", "皮疹"],
                False,
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
            state, _ = engine.create_session()
            failure = None
            try:
                reply = engine.submit(state.session_id, UserInput(text))
                checks["actual_requested_form"] = (
                    reply.kind == "calculation_input"
                    and reply.calculation_input["capability_id"] == identity
                )
                assert checks["actual_requested_form"]
                source = reply.calculation_followup["source_turn_id"]
                checks["full_original_pending"] = (
                    reply.calculation_followup["status"] == "pending"
                    and reply.calculation_followup["text"] == text
                )
                assert checks["full_original_pending"]
                before_facts = state.to_dict()["facts"]
                calls = gateway.metrics()["attempted_calls"]
                reply = engine.submit(
                    state.session_id,
                    UserInput(
                        "核对参数",
                        "confirm_calculation",
                        reply.action_id,
                        {
                            "proposal_id": reply.calculation_input["proposal_id"],
                            "input": values,
                        },
                    ),
                )
                checks["registered_result"] = (
                    reply.kind == "calculation_result"
                    and reply.calculation_result["data"]["value"] == expected
                )
                checks["confirmation_uses_no_provider_or_fact_write"] = (
                    gateway.metrics()["attempted_calls"] == calls
                    and state.to_dict()["facts"] == before_facts
                )
                assert (
                    checks["registered_result"]
                    and checks["confirmation_uses_no_provider_or_fact_write"]
                )
                execution = reply.calculation_result["execution_id"]
                if inject:
                    with gateway.lock:
                        gateway.fail_next = True
                reply = engine.submit(
                    state.session_id,
                    UserInput(
                        "继续处理补充",
                        "resume_after_calculation",
                        reply.action_id,
                        {"execution_id": execution},
                    ),
                )
                if inject:
                    checks["injected_failure_keeps_original_and_single_result"] = (
                        reply.kind == "service_error"
                        and reply.calculation_followup["status"] == "pending"
                        and state.pending_input == text
                        and len(state.calculations) == 1
                    )
                    assert checks["injected_failure_keeps_original_and_single_result"]
                    reply = engine.submit(
                        state.session_id,
                        UserInput("重试这一轮", "retry_turn", reply.action_id),
                    )
                current = state.facts.get(field)
                checks["clinical_supplement_has_original_source"] = (
                    current is not None
                    and current.assertion == Assertion.PRESENT
                    and current.turn_id == source
                    and all(t in (current.evidence or "") for t in tokens)
                )
                checks["returns_a_real_followup"] = (
                    reply.kind in {"question", "clarification", "summary"}
                    and reply.calculation_followup["status"] == "forwarded"
                )
                checks["no_duplicate_calculation"] = (
                    len(state.calculations) == 1
                    and state.calculations[0]["execution_id"] == execution
                    and state.pending_calculation is None
                )
                checks["pending_source_released"] = state.calculation_feedback is None
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
        stable = unchanged()
        report = {
            "evaluation": "calculation-supplements/v1",
            "synthetic_only": True,
            "scope": "Two fresh mixed patient inputs through official DSH and actual provider, host confirmation and mixed-source resumption; one injected local 503 is not an upstream paid request. Not a full clinical consultation or clinical validation.",
            "cases": cases,
            "frozen_sources_unchanged": stable,
            **gateway.metrics(),
            "passed": len(cases) == 2 and all(c["passed"] for c in cases) and stable,
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
                    "injected_failures": report["injected_failures"],
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
