"""Real DSH decisions for two synthetic, version-bound location corrections."""
import argparse
from copy import deepcopy
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
from medagent.env_config import for_backend, read_env
from test_live_desktop import EvaluationGateway
from tests.test_location_revisions import LocationRevisionTests


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
            and hashlib.sha256((root / p).read_bytes()).hexdigest() == h
            for p, h in frozen.items()
        )

    assert unchanged()
    spent = 0
    for p in args.output.parent.glob("*.json"):
        try:
            prior = json.loads(p.read_text())
            if prior.get("evaluation") == "location-revisions/v1":
                spent += int(prior["attempted_calls"])
        except (OSError, ValueError, TypeError, KeyError):
            continue
    maximum = min(12, 24 - spent)
    if maximum <= 0:
        raise SystemExit("Cumulative 24-call limit reached")
    settings = for_backend(read_env(root / ".env"), "dsh")
    token = secrets.token_hex(32)
    gateway = EvaluationGateway(settings, maximum, token)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_API_KEY": token,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
    }
    cases, failure = [], None
    scenarios = [
        ("explicit_left", "刚才不适位置选错了，请改为左侧太阳穴附近，范围仍是一片。"),
        ("uncertain_side", "刚才部位记错了，具体哪边还是说不清，先重新核对位置。"),
    ]
    try:
        for name, text in scenarios:
            fixture = LocationRevisionTests()
            fixture.setUp()
            engine, state = fixture.engine, fixture.state
            case = {
                "id": name,
                "input": text,
                "seed": state.to_dict(),
                "checks": {},
                "contexts": [],
                "passed": False,
            }
            cases.append(case)
            actual = DshBackend(engine.events, root, config)

            class RecordedBackend:
                name = "dsh"

                def decide_cancellable(self, identity, messages, cancelled):
                    case["contexts"].append(
                        {
                            "initial": json.loads(messages[1]["content"]),
                            "last_tool_result": json.loads(messages[-1]["content"])
                            if len(messages) > 2
                            else None,
                        }
                    )
                    return actual.decide_cancellable(
                        identity, deepcopy(messages), cancelled
                    )

            engine.backend = RecordedBackend()

            def check(name, condition):
                case["checks"][name] = bool(condition)
                if not condition:
                    raise AssertionError(name)

            try:
                reply = engine.submit(
                    state.session_id,
                    UserInput(
                        text, expected_action_id=fixture.original_reply.action_id
                    ),
                )
                case["draft_reply"] = reply.to_dict()
                check(
                    "original_fact_unchanged_before_confirmation",
                    state.facts["symptom_location"].to_dict() == fixture.original,
                )
                check(
                    "no_associated_symptom_substitution",
                    "associated_symptoms" not in state.facts,
                )
                check(
                    "actual_revision_form_present",
                    reply.kind == "self_check"
                    and (reply.task or {}).get("location_revision"),
                )
                spec = reply.task["answer_spec"]
                prefill = spec.get("prefill", {})
                answers = prefill.get("answers", {})
                if name == "explicit_left":
                    check(
                        "explicit_changes_are_reviewable",
                        prefill.get("origin") == "revision_proposal"
                        and answers.get("side") == "left"
                        and answers.get("region") == "head",
                    )
                    check(
                        "unmodified_dimensions_keep_separate_baseline",
                        prefill.get("baseline", {}).get("answers", {}).get("side")
                        == "right"
                        and answers.get("extent") == "area",
                    )
                    payload = fixture.flow.payload(reply)
                    payload["answers"]["side"] = "left"
                    count = gateway.metrics()["attempted_calls"]
                    completed = fixture.flow.confirm(reply, payload)
                    result = state.facts["symptom_location"]
                    check(
                        "confirmed_revision_has_history_and_actual_new_source",
                        result.supersedes == fixture.original["fact_id"]
                        and result.structured_input["revision_of"]
                        == fixture.original["fact_id"]
                        and result.structured_input["answers"]["side"]["value"]
                        == "left"
                        and "左右：左侧" in result.evidence
                        and result.quality.value == "not_assessed",
                    )
                    check(
                        "host_question_resumes_without_extra_model_calls",
                        completed.question_id == "onset"
                        and gateway.metrics()["attempted_calls"] == count,
                    )
                else:
                    check(
                        "uncertain_side_is_not_carried_forward_as_certain",
                        answers.get("side") in {None, "unknown"},
                    )
                    count = gateway.metrics()["attempted_calls"]
                    completed = fixture.cancel(reply)
                    check(
                        "cancel_keeps_original_record_and_question",
                        state.facts["symptom_location"].to_dict() == fixture.original
                        and completed.question_id == "onset"
                        and gateway.metrics()["attempted_calls"] == count,
                    )
                case["final_reply"] = completed.to_dict()
            except Exception as exc:
                case["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            finally:
                case["final"] = state.to_dict()
                case["events"] = [
                    e.to_dict() for e in engine.events.for_session(state.session_id)
                ]
                case["passed"] = "failure" not in case and all(case["checks"].values())
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        gateway.shutdown()
        gateway.server_close()
        report = {
            "evaluation": "location-revisions/v1",
            "version": __version__,
            "scope": "Two directed synthetic cases: scripted existing record, real subsequent DSH decisions, explicit synthetic patient confirmation/cancellation. Not a fresh full consultation or clinical validity.",
            "cases": cases,
            "failure": failure,
            "frozen_source_unchanged": unchanged(),
            **gateway.metrics(),
        }
        report["passed"] = (
            failure is None
            and len(cases) == len(scenarios)
            and all(c["passed"] for c in cases)
            and report["frozen_source_unchanged"]
        )
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
                            "id": c["id"],
                            "passed": c["passed"],
                            "failure": c.get("failure"),
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
