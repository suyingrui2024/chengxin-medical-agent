"""Bounded real-provider checks for normal intake and preemption of buffered replies."""

import argparse
import hashlib
import json
from pathlib import Path
import threading
import time

from benchmark_execution import BoundedGateway, BenchmarkGatewayHandler
from medagent.agent import AdaptiveConsultationEngine
from medagent.backends import DshBackend
from medagent.consultation import UserInput
from medagent.env_config import read_env, for_backend
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from medagent import __version__

ROOT = Path(__file__).resolve().parents[1]
CASES = ["normal-intake", "raw-priority", "requested-help"]


class BufferedResponse(BenchmarkGatewayHandler):
    def respond(self, status, data, mime):
        if self.server.buffer_reply and status == 200:
            self.server.buffered.set()
            if not self.server.release.wait(75):
                raise TimeoutError("Buffered provider fixture was not released")
        return super().respond(status, data, mime)


def fingerprints():
    files = [ROOT / "package.json", ROOT / "package-lock.json"]
    for name in ["src/medagent", "integrations/dsh", "desktop", "scripts"]:
        files += [
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file()
            and p.suffix
            in {".py", ".js", ".mjs", ".cjs", ".json", ".yml", ".html", ".css", ".svg"}
        ]
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(files))
    }


def run_case(name, settings, directory):
    directory.mkdir(parents=True)
    gateway = BoundedGateway(settings, 8, directory / "requests.json")
    gateway.buffer_reply = name != "normal-intake"
    gateway.buffered, gateway.release = threading.Event(), threading.Event()
    gateway.RequestHandlerClass = BufferedResponse
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    events = EventStore()
    backend = DshBackend(events, ROOT, config)
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=events,
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    state, opening = engine.create_session()
    result = {"case": name, "checks": {}}
    replies, errors = [], []

    def ordinary():
        try:
            replies.append(
                engine.submit(
                    state.session_id,
                    UserInput(
                        "我自己32岁，咳嗽两天，想整理信息。", expected_action_id=opening.action_id
                    ),
                )
            )
        except Exception as error:
            errors.append(type(error).__name__)

    thread = threading.Thread(target=ordinary, daemon=True)
    try:
        thread.start()
        if name == "normal-intake":
            thread.join(100)
            result["checks"] = {
                "request_finished": not thread.is_alive(),
                "no_controller_error": not errors,
                "reported_age_recorded": state.get("age_years") == 32,
                "reported_subject_recorded": state.get("consult_for") == "self",
                "chief_recorded": "咳嗽" in str(state.get("chief_complaint", "")),
                "onset_recorded": any(
                    value in str(state.get("onset", "")) for value in ("两天", "2天")
                ),
                "question_returned": bool(replies and replies[0].kind == "question"),
            }
        else:
            if not gateway.buffered.wait(65):
                raise TimeoutError("No successful real provider response was buffered")
            result["checks"]["real_response_buffered_before_priority"] = True
            text, choice = (
                ("现在严重呼吸困难", None)
                if name == "raw-priority"
                else ("需要先显示求助信息", "request_emergency_help")
            )
            started = time.monotonic()
            reply = engine.submit(state.session_id, UserInput(text, choice, "obsolete"))
            result["priority_latency_ms"] = round((time.monotonic() - started) * 1000)
            thread.join(2)
            revision = state.revision
            result["checks"].update(
                {
                    "priority_under_one_second": result["priority_latency_ms"] < 1000,
                    "emergency_route": reply.kind == "emergency"
                    and state.closed_reason == "emergency_route",
                    "original_request_unblocked_before_provider_release": not thread.is_alive(),
                    "same_priority_reply": bool(
                        replies and replies[0].action_id == reply.action_id
                    ),
                    "no_late_facts": not state.facts,
                    "no_invented_screen_fact": "emergency_screen" not in state.facts,
                }
            )
            gateway.release.set()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and (
                backend._children
                or not any(
                    e.event_type == "agent.decision_discarded"
                    for e in events.for_session(state.session_id)
                )
            ):
                time.sleep(0.05)
            result["checks"].update(
                {
                    "bridge_reaped": not backend._children,
                    "discard_recorded": any(
                        e.event_type == "agent.decision_discarded"
                        for e in events.for_session(state.session_id)
                    ),
                    "state_stable_after_release": state.revision == revision
                    and not state.facts,
                }
            )
    except Exception as error:
        result["error_type"] = type(error).__name__
        result["diagnostic"] = str(error)[:500]
        result["checks"]["scenario_finished"] = False
    finally:
        gateway.release.set()
        backend.close()
        thread.join(3)
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
    result["gateway"] = gateway.metrics()
    result["checks"].update(
        {
            "bounded_requests": len(gateway.calls) <= 8,
            "all_provider_http200": bool(gateway.calls)
            and all(c["status"] == 200 for c in gateway.calls),
        }
    )
    result["state"] = state.to_dict()
    result["replies"] = [reply.to_dict() for reply in replies]
    result["events"] = []
    for event in events.for_session(state.session_id):
        value = event.to_dict()
        if event.event_type == "model.responded":
            value["payload"] = {
                k: v for k, v in value["payload"].items() if k != "runtime_events"
            }
        result["events"].append(value)
    result["passed"] = bool(result["checks"]) and all(result["checks"].values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-total-calls", type=int, default=48)
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "cases": CASES,
                    "maximum_requests_per_case": 8,
                    "maximum_plan_requests": 24,
                    "external_requests": 0,
                }
            )
        )
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if (
        output.exists()
        or artifacts.exists()
        or output.suffix != ".json"
        or not 24 <= args.max_total_calls <= 48
    ):
        raise SystemExit("Fresh JSON output and total budget 24..48 required")
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if prior + 24 > args.max_total_calls:
        raise SystemExit("Cumulative directory budget insufficient")
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    source = fingerprints()
    artifacts.mkdir(parents=True)
    for name, expected in source.items():
        target = artifacts / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (ROOT / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Source drift during freeze")
        target.write_bytes(data)
    report = {
        "kind": "priority-help-live/v1",
        "runtime_version": __version__,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "source_sha256": source,
        "scope": "Synthetic engineering checks; no clinical accuracy, general coding parity or improved real-model efficiency claim.",
        "maximum_calls": args.max_total_calls,
        "prior_directory_calls": prior,
        "runs": [],
    }

    def save():
        report["attempted_calls"] = sum(
            r["gateway"]["attempted_calls"] for r in report["runs"]
        )
        report["source_unchanged"] = fingerprints() == source
        report["passed"] = (
            len(report["runs"]) == len(CASES)
            and all(run["passed"] for run in report["runs"])
            and report["source_unchanged"]
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise ValueError("Credential detected; refusing report")
        output.write_text(encoded + "\n")

    save()
    try:
        for name in CASES:
            if fingerprints() != source:
                raise ValueError("Frozen source changed")
            result = run_case(name, settings, artifacts / name)
            report["runs"].append(result)
            save()
            print(
                json.dumps(
                    {
                        "case": name,
                        "passed": result["passed"],
                        "calls": result["gateway"]["attempted_calls"],
                    }
                ),
                flush=True,
            )
    finally:
        save()
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
