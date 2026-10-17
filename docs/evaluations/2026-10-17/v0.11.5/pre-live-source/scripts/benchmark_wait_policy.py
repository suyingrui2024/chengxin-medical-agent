"""Matched real-model wait-policy ablation on two explicitly known development tasks."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
import threading
import time

from benchmark_execution import BoundedGateway
from benchmark_public_test_sample import task_prompt, POLICY
from execution_task_contract import CONTRACT, regression_file
from medagent.env_config import read_env, for_backend
from medagent.execution import ExecutionManager
from public_execution_cases import protected
from public_test_sample import (
    ROOT,
    FIXTURES,
    inventory,
    run_code,
    code_for,
    parse,
    classify,
)

# Reuse the already exercised bounded, no-follow candidate reader, unchanged.
TREE_SOURCE = ROOT / "docs/evaluations/2026-10-16/v0.10.2/candidate_tree.py"
sys.dont_write_bytecode = True
sys.path.insert(0, str(TREE_SOURCE.parent))
from candidate_tree import scan, regular_bytes, rebuild

CASES = ["sympy__sympy-21627", "sphinx-doc__sphinx-8627"]
CALLS, SECONDS, ORDER_SEED = 20, 600, 2026101603
MAXIMUM = 160


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprints():
    paths = [ROOT / "package.json", ROOT / "package-lock.json", TREE_SOURCE]
    for name in [
        "src/medagent",
        "integrations/dsh",
        "scripts",
        "benchmarks/swe-lite-test-sample",
    ]:
        paths.extend(
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file()
            and p.suffix
            in {".py", ".js", ".mjs", ".json", ".yml", ".html", ".css", ".svg", ".txt"}
        )
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def make_control(destination):
    destination.mkdir()
    shutil.copytree(ROOT / "integrations", destination / "integrations")
    shutil.copy2(ROOT / "package.json", destination / "package.json")
    (destination / "node_modules").symlink_to(
        ROOT / "node_modules", target_is_directory=True
    )
    patch = destination / "integrations/dsh/execution.patch.yml"
    value = patch.read_text()
    expected = "Explicit job_output waits may last up to 600 seconds within the shared task deadline,"
    if value.count("maxWaitTimeoutMs: 600000") != 1 or value.count(expected) != 1:
        raise ValueError("Unexpected candidate wait policy")
    value = value.replace(
        "maxWaitTimeoutMs: 600000", "maxWaitTimeoutMs: 30000"
    ).replace(
        expected,
        "Explicit job_output waits may last up to 30 seconds within the shared task deadline,",
    )
    patch.write_text(value)
    for original in (ROOT / "integrations").rglob("*"):
        if not original.is_file() or "__pycache__" in original.parts:
            continue
        relative = original.relative_to(ROOT)
        if relative.as_posix() != "integrations/dsh/execution.patch.yml":
            if digest(original) != digest(destination / relative):
                raise ValueError("Unexpected control runtime difference")
    return {
        "source_root": str(destination),
        "patch_sha256": digest(patch),
        "differences": [
            "explicit job_output maxWaitTimeoutMs 600000 -> 30000",
            "matching explicit-wait system instruction 600 -> 30 seconds",
        ],
        "unchanged": "All other integration files and the host Python controller are identical.",
    }


def file_checks(tree, baseline):
    current = tree["files"]
    added = (set(current) | set(tree["links"]) | set(tree["special"])) - set(baseline)
    return {
        "bounded_regular_candidate": not tree["special"],
        "upstream_tests_and_config_preserved": all(
            current.get(name) == expected
            for name, expected in baseline.items()
            if protected(name)
        ),
        "no_new_protected_files": not any(protected(name) for name in added),
        "no_new_test_control_files": not any(
            Path(name).name in CONTRACT["forbidden_new_basenames"] for name in added
        ),
        "new_tests_follow_contract": not any(
            (
                Path(name).name.startswith("test_")
                or Path(name).name.endswith("_test.py")
                or Path(name).name == "test.py"
            )
            and name.endswith(".py")
            and not regression_file(name)
            for name in added
        ),
        "python_source_changed": any(
            name.endswith(".py")
            and not protected(name)
            and baseline.get(name) != expected
            for name, expected in current.items()
        ),
    }


def save_candidate(directory, output, tree, baseline):
    changed = {
        name: expected
        for name, expected in tree["files"].items()
        if baseline.get(name) != expected
    }
    for name, expected in changed.items():
        data = regular_bytes(directory / name)
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Candidate changed after runtime shutdown")
        target = output / "candidate" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    overlay = {
        "files": changed,
        "deleted": sorted(set(baseline) - set(tree["files"]) - set(tree["links"])),
        "links": tree["links"],
        "special": tree["special"],
    }
    (output / "overlay.json").write_text(json.dumps(overlay, indent=2) + "\n")
    return overlay


def evaluate(
    item, runtime_root, environment, instance, mapping, settings, output, prompt
):
    output.mkdir()
    source = ROOT / environment["prepared_source"]
    baseline = environment["source_sha256"]
    if inventory(source) != baseline:
        raise ValueError("Prepared source drift")
    gateway = BoundedGateway(
        settings, CALLS, output / "requests.json", capture_tools=True
    )
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = ExecutionManager(root=output / "workspaces", source_root=runtime_root)
    result = {
        **item,
        "runtime_completed": False,
        "source_correct": None,
        "checks": {},
        "snapshots": [],
    }
    started = time.monotonic()
    runner = None
    directory = None
    try:
        runner = manager.create(config)
        directory = runner.directory
        shutil.copytree(source, directory, dirs_exist_ok=True)
        runner.start_turn(prompt, CALLS, SECONDS)
        deadline, events, answered = time.monotonic() + SECONDS + 15, {}, set()
        while time.monotonic() < deadline:
            snapshot = runner.snapshot(after=max(events, default=0))
            events.update({event["sequence"]: event for event in snapshot["events"]})
            for question in snapshot.get("clarifications", []):
                if question["status"] == "pending" and question["id"] not in answered:
                    answered.add(question["id"])
                    runner.answer_clarification(
                        question["id"], question["turn_id"], POLICY
                    )
            if (
                snapshot["status"] not in {"starting", "running", "cancelling"}
                and not runner.has_pending_clarification()
            ):
                break
            time.sleep(0.1)
        else:
            runner.cancel()
            raise TimeoutError("Task exceeded shared deadline")
        snapshot = runner.snapshot()
        events.update({event["sequence"]: event for event in snapshot["events"]})
        snapshot["events"] = [events[key] for key in sorted(events)]
        result["snapshots"] = [snapshot]
        result["runtime_completed"] = snapshot["status"] == "completed"
    except Exception as error:
        result["execution_error"] = {
            "type": type(error).__name__,
            "diagnostic": str(error)
            .replace(settings["MEDAGENT_LLM_API_KEY"], "[REDACTED]")
            .replace(gateway.token, "[REDACTED]")[:1000],
        }
    finally:
        manager.close()
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        result["gateway"] = gateway.metrics()
        result["execution_duration_ms"] = round((time.monotonic() - started) * 1000)
    result["checks"] = {
        "request_bound_respected": len(gateway.calls) <= CALLS,
        "all_provider_responses_ok": bool(gateway.calls)
        and all(call["status"] == 200 for call in gateway.calls),
    }
    try:
        if directory is None:
            raise ValueError("No candidate directory")
        tree = scan(directory)
        result["candidate_tree"] = tree
        result["checks"].update(file_checks(tree, baseline))
        result["overlay"] = save_candidate(directory, output, tree, baseline)
        with tempfile.TemporaryDirectory(
            prefix="wait-policy-oracle-", dir=output
        ) as raw:
            temporary = Path(raw)
            rebuilt = rebuild(
                source,
                directory,
                tree,
                baseline,
                temporary / "workspace",
                protected,
                CONTRACT["forbidden_new_basenames"],
            )
            verification = classify(
                parse(
                    run_code(
                        rebuilt,
                        temporary / "state",
                        code_for(instance, "candidate", mapping),
                    )
                )
            )
        result["verification"] = verification
        if verification.get("runtime_completed") and not verification.get(
            "setup_error"
        ):
            result["source_correct"] = verification["passed"]
        result["checks"]["independent_verification_available"] = (
            result["source_correct"] is not None
        )
        # Only our new, stopped per-run working copy is removed; all changed
        # regular bytes, link text, deletions, outcomes and SDK state remain.
        if runner.process is not None and runner.process.poll() is None:
            raise RuntimeError("Runtime still running; refusing working-copy cleanup")
        shutil.rmtree(directory)
        result["disposable_workspace_removed_after_overlay"] = True
    except Exception as error:
        result["verification_error"] = {
            "type": type(error).__name__,
            "diagnostic": str(error)[:1000],
        }
        result["checks"]["independent_verification_available"] = False
    result["source_and_contract_passed"] = result["source_correct"] is True and all(
        result["checks"].values()
    )
    result["end_to_end_completed"] = (
        result["source_and_contract_passed"] and result["runtime_completed"]
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    randomizer = random.Random(ORDER_SEED)
    plan = []
    first_order = ["wait-30", "wait-600"]
    randomizer.shuffle(first_order)
    for repetition in [1, 2]:
        for case_index, identity in enumerate(CASES):
            arms = (
                first_order
                if (repetition - 1 + case_index) % 2 == 0
                else first_order[::-1]
            )
            plan.extend(
                {"case": identity, "repetition": repetition, "arm": arm} for arm in arms
            )
    if not args.live:
        print(
            json.dumps(
                {
                    "plan": plan,
                    "maximum_requests": MAXIMUM,
                    "calls_per_run": CALLS,
                    "seconds_per_run": SECONDS,
                    "external_calls": 0,
                    "selection": "Two known development tasks selected for previously observed failures; no holdout or parity claim.",
                },
                ensure_ascii=False,
            )
        )
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if output.suffix != ".json" or output.exists() or artifacts.exists():
        raise SystemExit("Fresh JSON output required")
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if prior:
        raise SystemExit("Use a fresh parent budget directory; no hidden retries")
    preflight_path = ROOT / "docs/evaluations/2026-10-16/v0.10.2/preflight-05.json"
    preflight = json.loads(preflight_path.read_text())
    environment_path = FIXTURES / "environments-v5.json"
    if digest(environment_path) != preflight["environment_manifest_sha256"] or not all(
        preflight["cases"][identity]["qualified"] for identity in CASES
    ):
        raise SystemExit("Qualified frozen fixtures required")
    environments = json.loads(environment_path.read_text())["cases"]
    instances = {
        identity: json.loads((FIXTURES / identity / "instance.json").read_text())
        for identity in CASES
    }
    for identity in CASES:
        if (
            inventory(ROOT / environments[identity]["prepared_source"])
            != environments[identity]["source_sha256"]
        ):
            raise SystemExit("Prepared fixture drift")
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    artifacts.mkdir(parents=True)
    frozen = fingerprints()
    for name, expected in frozen.items():
        target = artifacts / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if digest(ROOT / name) != expected:
            raise ValueError("Source drift during freeze")
        shutil.copy2(ROOT / name, target)
    control = make_control(artifacts / "control")
    report = {
        "kind": "known-task-wait-policy-ablation/v1",
        "selection_reason": "Previously observed correct-but-unfinished SymPy and incorrect/unfinished Sphinx runs; known development tasks, not random or unseen.",
        "scope": "Only wait policy and matching instruction differ. Identical model, user prompts, prepared sources, 20 calls and 600 seconds. No official SDK arm or overall parity claim.",
        "model": settings["MEDAGENT_LLM_MODEL"],
        "plan": plan,
        "maximum_calls": MAXIMUM,
        "calls_per_run": CALLS,
        "seconds_per_run": SECONDS,
        "order_seed": ORDER_SEED,
        "control": control,
        "source_sha256": frozen,
        "preflight_sha256": digest(preflight_path),
        "prompts": {
            identity: task_prompt(instance) for identity, instance in instances.items()
        },
        "runs": [],
    }

    def save():
        report["attempted_calls"] = sum(
            row["gateway"]["attempted_calls"] for row in report["runs"]
        )
        report["frozen_source_unchanged"] = fingerprints() == frozen
        report["plan_finished"] = len(report["runs"]) == len(plan)
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise ValueError("Credential detected; refusing report")
        output.write_text(encoded + "\n")

    save()
    try:
        for item in plan:
            if (
                fingerprints() != frozen
                or digest(
                    Path(control["source_root"])
                    / "integrations/dsh/execution.patch.yml"
                )
                != control["patch_sha256"]
            ):
                raise ValueError("Frozen runtime changed")
            identity = item["case"]
            runtime = (
                ROOT if item["arm"] == "wait-600" else Path(control["source_root"])
            )
            folder = artifacts / f"r{item['repetition']}-{identity}-{item['arm']}"
            result = evaluate(
                item,
                runtime,
                environments[identity],
                instances[identity],
                preflight["cases"][identity]["test_id_map"],
                settings,
                folder,
                report["prompts"][identity],
            )
            report["runs"].append(result)
            save()
            print(
                json.dumps(
                    {
                        **item,
                        "calls": result["gateway"]["attempted_calls"],
                        "source_correct": result["source_correct"],
                        "runtime_completed": result["runtime_completed"],
                        "end_to_end_completed": result["end_to_end_completed"],
                    }
                ),
                flush=True,
            )
    finally:
        save()


if __name__ == "__main__":
    main()
