"""Bounded real DSH decisions for synthetic conversation during self-checks."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from medagent import __version__
from medagent.backends import DshBackend
from medagent.consultation import UserInput
from medagent.domain import Assertion
from medagent.env_config import for_backend, read_env
from test_live_desktop import EvaluationGateway
from tests.test_self_check_conversation import SelfCheckConversationTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh output required")
    root = Path(__file__).resolve().parents[1]
    frozen = json.loads(args.source_manifest.read_text())

    def unchanged():
        return all(
            (root / p).is_file()
            and hashlib.sha256((root / p).read_bytes()).hexdigest() == v
            for p, v in frozen.items()
        )

    assert unchanged()
    spent = 0
    for path in args.output.parent.glob("*.json"):
        try:
            report = json.loads(path.read_text())
            if report.get("evaluation") == "self-check-conversation/v1":
                spent += max(0, int(report["attempted_calls"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    maximum = min(18, 36 - spent)
    if maximum <= 0:
        raise SystemExit("Cumulative 36-call limit reached")
    settings = for_backend(read_env(root / ".env"), "dsh")
    token = secrets.token_hex(32)
    gateway = EvaluationGateway(settings, maximum, token)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_API_KEY": token,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
    }
    checks, cases = {}, []
    failure = None

    def seeded(task):
        fixture = SelfCheckConversationTests()
        fixture.setUp()
        reply = fixture.offer(task)
        engine, state = fixture.engine, fixture.state
        cases.append(
            {"task_id": task, "seed": state.to_dict(), "engine": engine, "state": state}
        )
        engine.backend = DshBackend(engine.events, root, config)
        return engine, state, reply

    def submit(engine, state, reply, text, choice=None):
        return engine.submit(state.session_id, UserInput(text, choice, reply.action_id))

    def resume(engine, state, reply):
        if reply.kind == "self_check":
            return reply
        choice = next(
            q.value
            for q in reply.quick_replies
            if q.value.startswith("resume_self_check:")
        )
        count = gateway.metrics()["attempted_calls"]
        reply = submit(engine, state, reply, "继续图示", choice)
        assert gateway.metrics()["attempted_calls"] == count
        return reply

    try:
        engine, state, reply = seeded("body_location")
        identity = reply.task["interaction_id"]
        text = "昨晚用过药物甲，用量不清楚。"
        reply = submit(engine, state, reply, text)
        medication = state.facts.get("medications_taken")
        checks["medication_is_recorded_without_location_pollution"] = bool(
            medication
            and medication.assertion != Assertion.ABSENT
            and "药物甲" in (medication.evidence or "")
            and "symptom_location" not in state.facts
            and reply.kind != "service_error"
        )
        assert checks["medication_is_recorded_without_location_pollution"]
        reply = resume(engine, state, reply)
        checks["original_task_occurrence_is_preserved"] = (
            reply.task["interaction_id"] == identity
        )
        assert checks["original_task_occurrence_is_preserved"]
        text = "右太阳穴附近疼。"
        reply = submit(engine, state, reply, text)
        candidate = (reply.task or {}).get("result_proposal")
        checks["natural_location_is_only_an_uncommitted_proposal"] = bool(
            candidate
            and candidate["option"] == "head"
            and candidate["evidence"] in text
            and "太阳穴" in candidate["evidence"]
            and "symptom_location" not in state.facts
        )
        assert checks["natural_location_is_only_an_uncommitted_proposal"]
        original = next(
            e.turn_id
            for e in reversed(engine.events.for_session(state.session_id))
            if e.event_type == "turn.user_message" and e.payload["text"] == text
        )
        reply = submit(engine, state, reply, "核对部位", candidate["choice"])
        location = state.facts.get("symptom_location")
        checks[
            "confirmation_records_original_quote_and_turn_without_quality_upgrade"
        ] = bool(
            location
            and location.value == "头/面部"
            and location.evidence == candidate["evidence"]
            and location.turn_id == original
            and location.quality.value == "not_assessed"
            and reply.kind != "service_error"
        )
        assert checks[
            "confirmation_records_original_quote_and_turn_without_quality_upgrade"
        ]

        engine, state, reply = seeded("axillary_temperature")
        reply = submit(engine, state, reply, "昨晚用过药物乙，药名和用量都记不准。")
        medication = state.facts.get("medications_taken")
        checks["temperature_task_accepts_history_without_fabricating_reading"] = bool(
            medication
            and "药物乙" in (medication.evidence or "")
            and "temperature_c" not in state.facts
            and not reply.input_error
            and reply.kind != "service_error"
        )
        assert checks["temperature_task_accepts_history_without_fabricating_reading"]
        reply = resume(engine, state, reply)
        reply = submit(engine, state, reply, "37.2℃")
        checks["resumed_temperature_still_uses_actual_reading"] = bool(
            state.get("temperature_c") == 37.2 and reply.kind != "service_error"
        )
        assert checks["resumed_temperature_still_uses_actual_reading"]

        engine, state, reply = seeded("body_location")
        reply = submit(engine, state, reply, "昨晚用过药物丙；这不是在描述不适的位置。")
        checks["explicit_non_location_is_not_proposed_as_location"] = bool(
            "symptom_location" not in state.facts
            and not (reply.task or {}).get("result_proposal")
            and reply.kind != "service_error"
        )
        assert checks["explicit_non_location_is_not_proposed_as_location"]

        engine, state, reply = seeded("body_location")
        reply = submit(engine, state, reply, "可能是右太阳穴附近疼，具体位置也不确定。")
        checks["uncertain_location_is_not_promoted_to_a_positive_proposal"] = bool(
            "symptom_location" not in state.facts
            and not (reply.task or {}).get("result_proposal")
            and reply.kind != "service_error"
        )
        assert checks["uncertain_location_is_not_promoted_to_a_positive_proposal"]
    except Exception as exc:
        failure = type(exc).__name__
    finally:
        gateway.shutdown()
        gateway.server_close()
        checks["frozen_source_unchanged"] = unchanged()
        for case in cases:
            state, engine = case.pop("state"), case.pop("engine")
            case.update(
                final=state.to_dict(),
                prefix_sha256=engine.prefix_sha256,
                events=[
                    e.to_dict() for e in engine.events.for_session(state.session_id)
                ],
            )
        report = {
            "evaluation": "self-check-conversation/v1",
            "version": __version__,
            "synthetic_only": True,
            "scope": "Scripted setup of four cases; subsequent decisions use official DSH and actual provider. Directed engineering cases, not complete live consultations, clinical validity or population accuracy.",
            "checks": checks,
            "failure_type": failure,
            "cases": cases,
            **gateway.metrics(),
            "passed": failure is None and all(checks.values()),
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
                    k: report[k]
                    for k in [
                        "version",
                        "passed",
                        "checks",
                        "attempted_calls",
                        "failure_type",
                    ]
                }
            )
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
