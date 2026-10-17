"""Bounded synthetic DSH observation evaluation. Credentials stay in the parent gateway."""
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
from test_live_desktop import EvaluationGateway


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a fresh output path")
    root = Path(__file__).resolve().parents[1]
    frozen = json.loads(args.source_manifest.read_text())

    def unchanged():
        return all(
            (root / name).is_file()
            and hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
            for name, digest in frozen.items()
        )

    assert unchanged(), "Source differs from the pre-run snapshot"
    settings = for_backend(read_env(root / ".env"), "dsh")
    token = secrets.token_hex(32)
    gateway = EvaluationGateway(settings, 12, token)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_API_KEY": token,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
    }
    events, checks, state = EventStore(), {}, None
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=events,
        ranker=OrderedQuestionRanker(),
        backend=DshBackend(events, root, config),
    )
    failure = None
    try:
        state, _ = engine.create_session()
        text = "我自己32岁，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。"
        reply = engine.submit(state.session_id, UserInput(text))
        checks["actual_applicable_task_offered"] = (
            reply.kind == "self_check" and reply.task["id"] == "axillary_temperature"
        )
        assert checks["actual_applicable_task_offered"]
        reply = engine.submit(
            state.session_id, UserInput("腋温38.25℃", expected_action_id=reply.action_id)
        )
        checks["first_report_recorded"] = state.get("temperature_c") == 38.25
        checks["model_returned_a_continuation"] = reply.kind in {
            "question",
            "clarification",
            "summary",
        }
        assert (
            checks["first_report_recorded"] and checks["model_returned_a_continuation"]
        )
        prior = reply
        before = gateway.metrics()["attempted_calls"]
        reply = engine.submit(
            state.session_id,
            UserInput(
                "再测一次腋温", "repeat_self_check:axillary_temperature", reply.action_id
            ),
        )
        reply = engine.submit(
            state.session_id, UserInput("腋温37.6℃", expected_action_id=reply.action_id)
        )
        checks["original_interaction_restored"] = (
            reply.kind == prior.kind
            and reply.question_id == prior.question_id
            and (reply.message == prior.message or reply.kind == "summary")
        )
        checks["new_reading_replaces_current"] = state.get("temperature_c") == 37.6
        reply = engine.submit(
            state.session_id,
            UserInput(
                "再测一次腋温", "repeat_self_check:axillary_temperature", reply.action_id
            ),
        )
        reply = engine.submit(
            state.session_id,
            UserInput("可能是38.6℃，读数没看清", expected_action_id=reply.action_id),
        )
        checks["uncertain_attempt_preserves_old"] = (
            state.get("temperature_c") == 37.6
            and state.observation_attempts[-1]["status"] == "unconfirmed"
        )
        reply = engine.submit(
            state.session_id,
            UserInput("我刚刚又测了一次腋温40.1℃，想重新测量。", expected_action_id=reply.action_id),
        )
        checks["candidate_not_written_before_confirmation"] = (
            state.get("temperature_c") == 37.6
            and reply.task["repeat"]["candidate"]["value"] == 40.1
        )
        reply = engine.submit(
            state.session_id,
            UserInput(
                "核对并记录40.1℃",
                reply.task["repeat"]["candidate"]["choice"],
                reply.action_id,
            ),
        )
        checks["existing_risk_route_reassessed"] = (
            reply.kind == "urgent" and state.get("temperature_c") == 40.1
        )
        checks["repeat_flow_adds_no_provider_calls"] = (
            gateway.metrics()["attempted_calls"] == before
        )
        history = [f for f in state.history if f.key == "temperature_c"]
        checks["actual_history_and_supersession_preserved"] = (
            [f.value for f in history] == [38.25, 37.6, 40.1]
            and history[1].supersedes == history[0].fact_id
            and history[2].supersedes == history[1].fact_id
        )
        assert all(checks.values())
    except Exception as exc:
        failure = type(exc).__name__
    finally:
        gateway.shutdown()
        gateway.server_close()
        checks["source_unchanged_since_snapshot"] = unchanged()
        report = {
            "synthetic_only": True,
            "scope": "One new official DSH conversation followed by host-controlled repeated readings; not a model repeat-tool selection benchmark or clinical validation.",
            "checks": checks,
            "failure_type": failure,
            **gateway.metrics(),
            "state": state.to_dict() if state else None,
            "events": [
                event.to_dict() for event in events.for_session(state.session_id)
            ]
            if state
            else [],
            "passed": failure is None and all(checks.values()),
        }
        # Gateway tokens are disposable but also excluded from archived event text.
        encoded = json.dumps(report, ensure_ascii=False, indent=2).replace(
            token, "[evaluation-token-redacted]"
        )
        encoded = encoded.replace(
            settings["MEDAGENT_LLM_API_KEY"], "[provider-key-redacted]"
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n")
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "attempted_calls": report["attempted_calls"],
                    "checks": checks,
                    "failure_type": failure,
                }
            )
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
