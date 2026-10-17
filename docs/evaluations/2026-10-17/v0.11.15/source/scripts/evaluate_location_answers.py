"""Bounded real DSH evaluation of patient-confirmed location forms, synthetic only."""
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
from tests.test_self_check_conversation import SelfCheckConversationTests
from tests.test_agent import cmd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh output required")
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(args.source_manifest.read_text())

    def unchanged():
        return all(
            (root / p).is_file()
            and hashlib.sha256((root / p).read_bytes()).hexdigest() == digest
            for p, digest in manifest.items()
        )

    assert unchanged()
    spent = 0
    for path in args.output.parent.glob("*.json"):
        try:
            prior = json.loads(path.read_text())
            if prior.get("evaluation") == "location-answers/v1":
                spent += int(prior["attempted_calls"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
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
    cases = []
    failure = None
    scenarios = [
        ("complete_and_corrected", "头部右侧太阳穴附近，是一片范围的不适。", True, "area"),
        ("missing_extent", "右太阳穴附近疼。", True, None),
        ("uncertain_extent", "右太阳穴附近疼；范围说不清。", True, "unknown"),
        ("uncertain_location", "可能是右太阳穴附近疼，具体位置也不确定。", False, None),
    ]
    try:
        for name, text, detailed, extent in scenarios:
            case = {
                "id": name,
                "input": text,
                "checks": {},
                "model_contexts": [],
                "passed": False,
            }
            cases.append(case)
            fixture = SelfCheckConversationTests()
            fixture.setUp()
            engine, state = fixture.engine, fixture.state
            reply = fixture.offer()
            if detailed:
                fixture.backend.commands = [
                    cmd(
                        "clarify",
                        topic="symptom_location",
                        message="你说的头痛具体在头部哪个区域或哪一侧？包括范围是否清楚？",
                        answer_fields=["region", "area", "side", "extent"],
                    ),
                ]
                reply = fixture.submit("我想逐项核对位置")
            case["scripted_seed"] = state.to_dict()
            case["seed_reply"] = reply.to_dict()
            actual = DshBackend(engine.events, root, config)

            class RecordedBackend:
                name = "dsh"

                def decide_cancellable(self, identity, messages, cancelled):
                    case["model_contexts"].append(
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

            def check(label, condition):
                case["checks"][label] = bool(condition)
                if not condition:
                    raise AssertionError(label)

            try:
                reply = engine.submit(
                    state.session_id,
                    UserInput(text, expected_action_id=reply.action_id),
                )
                case["draft_reply"] = reply.to_dict()
                check(
                    "no_fact_before_patient_confirmation",
                    "symptom_location" not in state.facts,
                )
                check(
                    "no_associated_symptom_substitution",
                    "associated_symptoms" not in state.facts,
                )
                check("model_returned_an_action", reply.kind != "service_error")
                if not detailed:
                    check(
                        "uncertain_location_remains_in_graphical_task",
                        reply.kind == "self_check",
                    )
                    spec = (reply.task or {}).get("answer_spec", {})
                    draft = spec.get("prefill", {}).get("answers", {})
                    check(
                        "no_positive_side_from_uncertain_words",
                        draft.get("side") in (None, "unknown"),
                    )
                    area = draft.get("area")
                    check(
                        "no_positive_area_from_uncertain_words",
                        not area
                        or area.get("kind") in {"unknown", "text"}
                        and (
                            area.get("kind") == "unknown"
                            or "可能" in str(area.get("value"))
                        ),
                    )
                    question = (reply.task or {}).get("clarification", "")
                    fields = spec.get("fields", ["region"])
                    check(
                        "question_has_matching_answer_dimensions",
                        all(
                            not any(word in question for word in words)
                            or field in fields
                            for field, words in {
                                "side": ("哪一侧", "哪侧", "左右", "左侧", "右侧"),
                                "area": ("具体", "区域"),
                                "extent": ("范围", "一点", "一片"),
                            }.items()
                        ),
                    )
                    continue
                spec = (reply.task or {}).get("answer_spec", {})
                draft = spec.get("prefill", {})
                answers = draft.get("answers", {})
                check(
                    "model_proposed_reported_location_dimensions",
                    answers.get("region") == "head"
                    and answers.get("side") == "right"
                    and (
                        answers.get("area") == {"kind": "preset", "value": "temple"}
                        or answers.get("area", {}).get("kind") == "text"
                        and "太阳穴" in str(answers["area"].get("value"))
                    ),
                )
                check(
                    "extent_is_not_invented",
                    answers.get("extent") == extent
                    if extent is not None
                    else "extent" not in answers,
                )
                confirmed = deepcopy(answers)
                confirmed["extent"] = (
                    extent or "unknown"
                )  # Synthetic patient explicitly chooses this.
                if name == "complete_and_corrected":
                    confirmed[
                        "side"
                    ] = "left"  # Deliberate correction by synthetic patient.
                payload = {
                    "schema": spec["schema"],
                    "form_id": spec["id"],
                    "answers": confirmed,
                    "note": "",
                }
                case["synthetic_patient_confirmation"] = payload
                before_calls = gateway.metrics()["attempted_calls"]
                before_events = len(engine.events.for_session(state.session_id))
                reply = engine.submit(
                    state.session_id,
                    UserInput(
                        "核对定位答案",
                        "confirm_location_answer",
                        reply.action_id,
                        observation=payload,
                    ),
                )
                case["post_confirmation_calls"] = (
                    gateway.metrics()["attempted_calls"] - before_calls
                )
                result = state.facts.get("symptom_location")
                check(
                    "confirmed_controls_are_the_recorded_source",
                    result
                    and result.structured_input["answers"]["side"]["value"]
                    == confirmed["side"]
                    and result.quality.value == "not_assessed"
                    and result.source.value == "patient"
                    and "左右：" in result.evidence,
                )
                check(
                    "original_model_draft_has_separate_provenance",
                    result.structured_input["proposal_source"]["answers"] == answers
                    and result.structured_input["proposal_source"]["source_turn_id"]
                    != result.turn_id,
                )
                tools = [
                    e.payload
                    for e in engine.events.for_session(state.session_id)[before_events:]
                    if e.event_type == "tool.requested"
                ]
                check(
                    "no_repeat_or_observation_write_after_confirmation",
                    not any(
                        e["tool"]
                        in {
                            "propose_self_check_result",
                            "show_self_check",
                            "repeat_self_check",
                        }
                        or e["tool"] == "record_facts"
                        and any(
                            f.get("key") == "symptom_location"
                            for f in e["arguments"].get("facts", [])
                        )
                        for e in tools
                    )
                    and reply.kind != "service_error",
                )
                case["final_reply"] = reply.to_dict()
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
            "evaluation": "location-answers/v1",
            "version": __version__,
            "scope": "Four directed synthetic cases with scripted setup, real subsequent DSH decisions and explicit synthetic-patient confirmation. Not a fresh full consultation or clinical validation.",
            "frozen_source_unchanged": unchanged(),
            "failure": failure,
            "cases": cases,
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
