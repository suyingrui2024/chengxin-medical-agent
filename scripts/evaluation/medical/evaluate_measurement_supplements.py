"""Bounded real DSH decisions on a synthetic, explicitly seeded outpatient case."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import threading

from medagent.backends import DshBackend
from medagent.consultation import UserInput
from medagent.domain import Assertion
from medagent.env_config import for_backend, read_env
from scripts.evaluation.medical.test_live_desktop import EvaluationGateway
from tests.test_repeated_observations import measured_case, repeat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh output required")
    root = Path(__file__).resolve().parents[3]
    before = json.loads(args.source_manifest.read_text())

    def unchanged():
        return all(
            (root / n).is_file()
            and hashlib.sha256((root / n).read_bytes()).hexdigest() == d
            for n, d in before.items()
        )

    assert unchanged()
    spent = 0
    for p in args.output.parent.glob("*.json"):
        try:
            r = json.loads(p.read_text())
            if r.get("synthetic_only") is True:
                spent += max(0, int(r.get("attempted_calls", 0)))
        except (ValueError, OSError):
            pass
    maximum = min(14, 28 - spent)
    if maximum <= 0:
        raise SystemExit("Cumulative 28-call limit reached")
    settings = for_backend(read_env(root / ".env"), "dsh")
    token = secrets.token_hex(32)
    gateway = EvaluationGateway(settings, maximum, token)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_API_KEY": token,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
    }
    engine, _, state, reply = measured_case(summary=True)
    seeded = state.to_dict()
    engine.backend = DshBackend(engine.events, root, config)
    checks = {}
    failure = None

    def supported_medication(token_text):
        current = state.facts.get("medications_taken")
        return (
            current is not None
            and current.assertion != Assertion.ABSENT
            and token_text in (current.evidence or "")
        )

    try:
        offered = repeat(engine, state, reply)
        text = "腋温37.6℃；更正：昨晚用过药物甲，用量不清楚。"
        reply = engine.submit(
            state.session_id, UserInput(text, expected_action_id=offered.action_id)
        )
        checks["repeat_processes_medication_update"] = (
            supported_medication("药物甲")
            and state.get("temperature_c") == 37.6
            and reply.kind != "service_error"
        )
        assert checks["repeat_processes_medication_update"]
        first_id = state.facts["temperature_c"].fact_id
        offered = repeat(engine, state, reply)
        text = "腋温38.6℃，读数可能不准；更正：今天用过药物乙，用量不清楚。"
        reply = engine.submit(
            state.session_id, UserInput(text, expected_action_id=offered.action_id)
        )
        checks["uncertain_reading_preserves_old_fact"] = (
            state.facts["temperature_c"].fact_id == first_id
            and state.observation_attempts[-1]["status"] == "unconfirmed"
        )
        checks["uncertain_feedback_still_processes_medication"] = (
            supported_medication("药物乙") and reply.kind != "service_error"
        )
        assert (
            checks["uncertain_reading_preserves_old_fact"]
            and checks["uncertain_feedback_still_processes_medication"]
        )
        text = "想重新测量；更正：昨晚用过药物丙，用量不清楚。"
        reply = engine.submit(
            state.session_id, UserInput(text, expected_action_id=reply.action_id)
        )
        checks["history_before_repeat_request_is_not_lost"] = supported_medication(
            "药物丙"
        )
        checks["pending_repeat_is_shown_after_history"] = (
            reply.kind == "self_check" and reply.task["id"] == "axillary_temperature"
        )
        assert (
            checks["history_before_repeat_request_is_not_lost"]
            and checks["pending_repeat_is_shown_after_history"]
        )
        count = gateway.metrics()["attempted_calls"]
        reply = engine.submit(
            state.session_id, UserInput("腋温37.5℃", expected_action_id=reply.action_id)
        )
        checks["pure_reading_still_resumes_without_provider_call"] = (
            state.get("temperature_c") == 37.5
            and gateway.metrics()["attempted_calls"] == count
        )
        checks["no_duplicate_measurements"] = [
            f.value for f in state.history if f.key == "temperature_c"
        ] == [38.25, 37.6, 37.5]
        assert all(checks.values())
    except Exception as exc:
        failure = type(exc).__name__
    finally:
        gateway.shutdown()
        gateway.server_close()
        checks["frozen_sources_unchanged"] = unchanged()
        report = {
            "synthetic_only": True,
            "scope": "Seeded case from real controller plus scripted initial history; only subsequent supplement decisions use the official DSH and real provider. Not a complete new live consultation or clinical evaluation.",
            "seed_state": seeded,
            "checks": checks,
            "failure_type": failure,
            **gateway.metrics(),
            "state": state.to_dict(),
            "events": [
                e.to_dict() for e in engine.events.for_session(state.session_id)
            ],
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
                    "passed": report["passed"],
                    "checks": checks,
                    "attempted_calls": report["attempted_calls"],
                    "failure_type": failure,
                }
            )
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
