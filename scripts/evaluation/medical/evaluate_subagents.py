"""Run the subagent case set against medagent.subagents (offline stub).

The corpus is an *engineering* corpus (review_status: unreviewed in the
fixture): it verifies the deterministic validation pipeline of the
Python SubagentCoordinator from docs/联网与子智能体工具设计.md §8.2 —
schema discipline, verbatim evidence checking (a fabricated quote must
never survive: hard red line), the drop-rate fuse, staleness watermark
judgement, and case_digest quote fidelity. The model backend is a
scripted stub returning frozen subagent_report envelopes; no real model
is called. ``--live`` is reserved for manual runs against a configured
deployment and currently skips with an explicit notice.

Usage:
    python -m scripts.evaluation.medical.evaluate_subagents \
        [--cases tests/fixtures/subagent_cases_v0.json] \
        [--json-out report.json] [--live]

Exit codes: 0 iff every hard gate passes; 1 on a gate failure; 2 when
the module under test is unavailable (setup error, no traceback).
"""
from __future__ import annotations

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

EXPECTED_KINDS = {"evidence_lookup", "second_opinion", "case_digest", "followup_batch"}


class SetupFailure(Exception):
    """The module under test is missing or shaped unexpectedly."""


def _load_subagents():
    try:
        from medagent import subagents
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        raise SetupFailure(
            f"medagent.subagents is not available ({type(exc).__name__}: {exc}); "
            "the SubagentCoordinator (Phase C) must land before this evaluation can run"
        ) from exc
    if not callable(getattr(subagents, "SubagentCoordinator", None)):
        raise SetupFailure("medagent.subagents.SubagentCoordinator is missing")
    if not hasattr(subagents, "KINDS"):
        raise SetupFailure("medagent.subagents.KINDS is missing")
    return subagents


class ScriptedBackend:
    """decide_cancellable stub replaying a case's frozen script.

    Mirrors the stub doubles used for background_review tests: each call
    returns the next scripted reply (the last one repeats), and an
    optional hook fires on the first call (used to mutate the case
    mid-run for the staleness case).
    """

    name = "stub"

    def __init__(self, script, on_first_call=None):
        self._script = list(script)
        self._on_first_call = on_first_call
        self.calls = 0

    def decide_cancellable(self, session_id, messages, cancelled):
        self.calls += 1
        if self.calls == 1 and self._on_first_call is not None:
            self._on_first_call()
        if len(self._script) > 1:
            return self._script.pop(0)
        return self._script[0]

    def decide(self, session_id, messages):  # fallback surface
        return self.decide_cancellable(session_id, messages, None)


def _evidence_pool(case, state):
    pool = []
    for raw in case.get("facts", []):
        if raw.get("evidence"):
            pool.append(str(raw["evidence"]))
        if raw.get("value") is not None:
            pool.append(str(raw["value"]))
    for turn in state.transcript:
        if turn.get("content"):
            pool.append(str(turn["content"]))
    return pool


def _find_disposition(node):
    """Return the first structured disposition value in a result tree."""
    if isinstance(node, dict):
        for key in (
            "disposition_tendency",
            "disposition",
            "disposition_level",
            "tendency",
        ):
            value = node.get(key)
            if isinstance(value, str):
                return value
        for value in node.values():
            found = _find_disposition(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_disposition(item)
            if found is not None:
                return found
    return None


def _run_case(subagents, case):
    from medagent.domain import Assertion, Fact, Source
    from medagent.events import EventStore
    from scripts.evaluation.medical.evaluate_conclusion_sheet import build_state

    state, _summary = build_state(case)
    if case.get("transcript"):
        state.transcript = [dict(turn) for turn in case["transcript"]]

    on_first_call = None
    mutation = case.get("mutate_during_run")
    if mutation:

        def on_first_call(state=state, mutation=mutation):
            state.record(
                Fact(
                    key=mutation["key"],
                    value=mutation["value"],
                    assertion=Assertion(mutation["assertion"]),
                    source=Source.PATIENT,
                    turn_id="eval-stale",
                    evidence=mutation["evidence"],
                )
            )

    backend = ScriptedBackend(case["stub_script"], on_first_call=on_first_call)
    coordinator = subagents.SubagentCoordinator(
        EventStore(), backend, mode="advisory"
    )
    kind = case["task_kind"]
    fact_keys = [raw["key"] for raw in case.get("facts", [])]
    if kind == "case_digest":
        scope = {"fact_keys": fact_keys, "transcript": "recent_window", "prior_case": False}
    else:
        scope = {"fact_keys": fact_keys, "transcript": "evidence_only"}
    proposal = {
        "task_kind": kind,
        "objective": case.get("objective", ""),
        "branches": [
            {"branch_id": "b1", "objective": case.get("objective", ""), "scope": scope}
        ],
        "expected_output": "subagent_result/v1",
    }
    result = coordinator.run_spawn(state, proposal, session_id=state.session_id)
    branches = result.get("branches") or []
    branch = branches[0] if branches else {}
    validation = branch.get("validation") or {}
    findings = branch.get("findings") or []
    status = branch.get("status")

    # Independent leak audit: the harness re-verifies every surviving
    # case quote against the evidence pool instead of trusting the
    # coordinator's own validation counters.
    pool = _evidence_pool(case, state)
    leaks = 0
    for finding in findings:
        for item in finding.get("evidence") or []:
            if item.get("source_type") in (None, "case"):
                quote = item.get("quote") or ""
                if not any(quote in text for text in pool):
                    leaks += 1
    serialized = json.dumps(branch, ensure_ascii=False)
    fabricated_survived = sum(
        1 for quote in case.get("fabricated_quotes", []) if quote in serialized
    )

    expected = case.get("expected", {})
    failures = []
    if status != expected.get("status"):
        failures.append(f"status {status} != {expected.get('status')}")
    if kind == "case_digest":
        digest = branch.get("digest") or {}
        digest_quotes = [
            item.get("quote") for item in digest.get("confirmed_facts") or []
        ]
        if set(digest_quotes) != set(expected.get("digest_quotes", [])):
            failures.append(
                f"digest quotes {sorted(digest_quotes)} != "
                f"{sorted(expected.get('digest_quotes', []))}"
            )
        minimum = expected.get("min_contradictions")
        if minimum and len(digest.get("contradictions") or []) < minimum:
            failures.append("digest contradictions missing")
    else:
        surviving = len(findings)
        dropped = validation.get("dropped_claims")
        if surviving != expected.get("surviving_claims"):
            failures.append(
                f"surviving {surviving} != {expected.get('surviving_claims')}"
            )
        if dropped != expected.get("dropped_claims"):
            failures.append(f"dropped {dropped} != {expected.get('dropped_claims')}")
    disposition = _find_disposition(branch)
    disposition_check = "not_surfaced"
    if disposition is not None and case.get("expected_disposition"):
        disposition_check = (
            "matched" if disposition in case["expected_disposition"] else "mismatched"
        )
        if disposition_check == "mismatched":
            failures.append(f"disposition {disposition} outside expected set")

    expected_calls = case.get("expected_backend_calls", 1)
    return {
        "id": case.get("id"),
        "task_kind": kind,
        "ok": not failures,
        "failures": failures,
        "status": status,
        "backend_calls": backend.calls,
        "schema_first_pass": backend.calls == expected_calls,
        "leaks": leaks,
        "fabricated_total": len(case.get("fabricated_quotes", [])),
        "fabricated_survived": fabricated_survived,
        "disposition_check": disposition_check,
    }


def score_corpus(corpus) -> dict:
    """Score the whole corpus in offline stub mode; never raises for a
    missing module."""
    report = {
        "corpus": corpus.get("corpus"),
        "corpus_version": corpus.get("version"),
        "review_status": corpus.get("review_status"),
        "cases": 0,
        "passed": 0,
        "fatal": None,
        "gates": {},
        "gate_pass": False,
        "results": [],
    }
    try:
        subagents = _load_subagents()
    except SetupFailure as exc:
        report["fatal"] = str(exc)
        return report
    kinds = set(subagents.KINDS)
    kinds_ok = EXPECTED_KINDS <= kinds
    report["kinds"] = sorted(kinds)

    schema_cases = schema_passed = 0
    total_leaks = 0
    fabricated_total = fabricated_survived = 0
    stale_total = stale_correct = stale_false_positives = 0
    for case in corpus.get("cases", []):
        try:
            outcome = _run_case(subagents, case)
        except Exception as exc:  # one bad case must not sink the run
            outcome = {
                "id": case.get("id"),
                "task_kind": case.get("task_kind"),
                "ok": False,
                "failures": [f"{type(exc).__name__}: {exc}"],
                "status": None,
                "backend_calls": 0,
                "schema_first_pass": False,
                "leaks": 0,
                "fabricated_total": len(case.get("fabricated_quotes", [])),
                "fabricated_survived": 0,
                "disposition_check": "error",
            }
        report["results"].append(outcome)
        if not case.get("exclude_from_schema_rate"):
            schema_cases += 1
            schema_passed += int(outcome["schema_first_pass"])
        total_leaks += outcome["leaks"]
        fabricated_total += outcome["fabricated_total"]
        fabricated_survived += outcome["fabricated_survived"]
        if case.get("expected", {}).get("status") == "stale":
            stale_total += 1
            stale_correct += int(outcome["status"] == "stale")
        elif outcome["status"] == "stale":
            stale_false_positives += 1
    report["cases"] = len(report["results"])
    report["passed"] = sum(1 for item in report["results"] if item["ok"])

    schema_rate = schema_passed / schema_cases if schema_cases else 1.0
    fabricated_drop_rate = (
        1.0 - fabricated_survived / fabricated_total if fabricated_total else 1.0
    )
    stale_ok = stale_correct == stale_total and stale_false_positives == 0
    report.update(
        {
            "schema_first_pass_rate": round(schema_rate, 3),
            "unverified_quote_leaks": total_leaks,
            "fabricated_quote_drop_rate": round(fabricated_drop_rate, 3),
            "stale_cases": stale_total,
            "stale_correct": stale_correct,
            "stale_false_positives": stale_false_positives,
        }
    )
    report["gates"] = {
        "kinds_complete": kinds_ok,
        "schema_first_pass_rate==1.0": schema_rate == 1.0,
        "unverified_quote_leaks==0": total_leaks == 0,
        "fabricated_quote_drop_rate==1.0": fabricated_drop_rate == 1.0,
        "stale_judgement_correct": stale_ok,
    }
    report["gate_pass"] = all(report["gates"].values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases", default=str(ROOT / "tests" / "fixtures" / "subagent_cases_v0.json")
    )
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    if args.live:
        print(
            "SKIP: live subagent evaluation needs a configured model backend "
            "(deployment credentials); the release gate uses the offline "
            "stub mode. No cases were run."
        )
        return 0

    corpus = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    report = score_corpus(corpus)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    if report["fatal"]:
        print(f"evaluate_subagents: SETUP ERROR: {report['fatal']}", file=sys.stderr)
        return 2
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
