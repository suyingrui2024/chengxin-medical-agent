"""Frozen, balanced comparison on the preregistered four new-to-project public tasks."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import tempfile
import threading
import time

from benchmark_execution import Baseline, BoundedGateway
from benchmark_public_test_sample import task_prompt, POLICY
from benchmark_wait_policy import (
    file_checks,
    save_candidate,
    scan,
    rebuild,
    TREE_SOURCE,
)
from evaluate_delivery_checks import fingerprints
from execution_task_contract import CONTRACT, regression_file
from medagent import __version__
from medagent.env_config import read_env, for_backend
from medagent.execution import ExecutionManager
from public_execution_cases import protected
from public_test_sample import (
    ROOT,
    inventory,
    run_code,
    code_for,
    parse,
    classify,
    MARKER,
)

FIXTURES = ROOT / "benchmarks/swe-lite-transfer"
CALLS, SECONDS, MAXIMUM = 20, 600, 320


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inventory():
    values = fingerprints()
    values[str(TREE_SOURCE.relative_to(ROOT))] = digest(TREE_SOURCE)
    values.update(
        {str(p.relative_to(ROOT)): digest(p) for p in FIXTURES.rglob("*.json")}
    )
    return values


def regression_code(paths):
    return r"""import json,os,sys
from pathlib import Path
os.chdir('/workspace')
sys.path[:0]=['/workspace/.benchmark_dependencies','/workspace/src','/workspace/lib','/workspace']
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
os.environ['MPLBACKEND']='agg'
os.environ['PYTHONPATH']=os.pathsep.join(sys.path[:4])
if Path('/workspace/.benchmark_dependencies/_benchmark_native.py').is_file():import _benchmark_native
import pytest
class Collector:
 def __init__(self):self.items=[];self.outcomes={}
 def pytest_collection_modifyitems(self,session,config,items):self.items=[item.nodeid for item in items]
 def pytest_runtest_logreport(self,report):
  if report.when=='call' or report.failed or report.skipped:self.outcomes[report.nodeid]=report.outcome
collector=Collector()
paths=json.loads(PATHS)
code=int(pytest.main(['-q','--rootdir','/workspace','--tb=short','-ra','-p','pytest_mock','-p','no:cacheprovider',*paths],plugins=[collector]))
print('CHENGXIN_TEST_SAMPLE_RESULT='+json.dumps({'pytest_exit_code':code,'collected':collector.items,'outcomes':collector.outcomes}))
""".replace(
        "PATHS", repr(json.dumps(paths))
    )


def evaluate(
    item,
    environment,
    instance,
    mapping,
    settings,
    output,
    prompt,
    progress=lambda **kw: None,
):
    output.mkdir()
    prepared = ROOT / environment["prepared_source"]
    baseline = environment["source_sha256"]
    if inventory(prepared) != baseline:
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
    manager = runner = directory = None
    result = {
        **item,
        "runtime_completed": False,
        "implementation_passed": None,
        "checks": {},
        "snapshots": [],
    }
    started = time.monotonic()
    try:
        if item["arm"] == "product":
            manager = ExecutionManager(root=output / "workspaces", source_root=ROOT)
            runner = manager.create(config)
            directory = runner.directory
        else:
            directory = output / "workspace"
            directory.mkdir()
            (output / "state").mkdir()
        shutil.copytree(prepared, directory, dirs_exist_ok=True)
        progress(phase="running", workspace=str(directory))
        if item["arm"] == "official-sdk":
            runner = Baseline(
                directory,
                output / "state",
                config,
                entry_point=ROOT / "scripts/execution_test_baseline.mjs",
                time_budget_seconds=SECONDS,
            )
            progress(runtime_pid=runner.process.pid)
            runner.receive("ready", 30)
            snapshot = runner.run(prompt, CALLS)
        else:
            runner.start_turn(prompt, CALLS, SECONDS)
            deadline, events, answered = time.monotonic() + SECONDS + 15, {}, set()
            while time.monotonic() < deadline:
                snapshot = runner.snapshot(after=max(events, default=0))
                events.update({e["sequence"]: e for e in snapshot["events"]})
                if runner.process is not None:
                    progress(runtime_pid=runner.process.pid)
                for q in snapshot.get("clarifications", []):
                    if q["status"] == "pending" and q["id"] not in answered:
                        answered.add(q["id"])
                        runner.answer_clarification(q["id"], q["turn_id"], POLICY)
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
            events.update({e["sequence"]: e for e in snapshot["events"]})
            snapshot["events"] = [events[k] for k in sorted(events)]
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
        if manager:
            manager.close()
        elif runner:
            runner.close()
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        result["gateway"] = gateway.metrics()
        result["execution_duration_ms"] = round((time.monotonic() - started) * 1000)
    result["checks"] = {
        "request_bound_respected": len(gateway.calls) <= CALLS,
        "all_provider_responses_ok": bool(gateway.calls)
        and all(c["status"] == 200 for c in gateway.calls),
    }
    try:
        if directory is None:
            raise ValueError("No candidate directory")
        progress(phase="independent-verification")
        tree = scan(directory)
        result["candidate_tree"] = tree
        result["file_checks"] = file_checks(tree, baseline)
        result["overlay"] = save_candidate(directory, output, tree, baseline)
        with tempfile.TemporaryDirectory(prefix="transfer-oracle-", dir=output) as raw:
            temporary = Path(raw)
            workspace = rebuild(
                prepared,
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
                        workspace,
                        temporary / "state",
                        code_for(instance, "candidate", mapping),
                    )
                )
            )
            result["verification"] = verification
            if verification.get("runtime_completed") and not verification.get(
                "setup_error"
            ):
                result["implementation_passed"] = verification["passed"]
            added = [
                name
                for name in tree["files"]
                if name not in baseline and regression_file(name)
            ]
            if added:
                extra = parse(
                    run_code(
                        workspace,
                        temporary / "regression-state",
                        regression_code(sorted(added)),
                    )
                )
                collected = extra.get("collected", [])
                outcomes = extra.get("outcomes", {})
                extra["passed"] = (
                    extra.get("runtime_completed") is True
                    and extra.get("pytest_exit_code") == 0
                    and bool(collected)
                    and all(outcomes.get(node) == "passed" for node in collected)
                    and all(
                        any(node.startswith(path + "::") for node in collected)
                        for path in added
                    )
                )
                result["added_regressions"] = extra
            else:
                result["added_regressions"] = {
                    "passed": True,
                    "collected": [],
                    "scope": "No allowed new regression files; no generated-test claim.",
                }
        result["checks"]["independent_verification_available"] = (
            result["implementation_passed"] is not None
        )
        result["checks"]["added_regressions_passed"] = result["added_regressions"][
            "passed"
        ]
        if (
            runner is not None
            and runner.process is not None
            and runner.process.poll() is None
        ):
            raise ValueError("Candidate runtime still live")
        shutil.rmtree(directory)
        result["disposable_workspace_removed_after_overlay"] = True
    except Exception as error:
        result["verification_error"] = {
            "type": type(error).__name__,
            "diagnostic": str(error)[:1000],
        }
        result["checks"]["independent_verification_available"] = False
    result["source_and_contract_passed"] = (
        result["implementation_passed"] is True
        and all(result.get("file_checks", {}).values())
        and bool(result.get("file_checks"))
        and all(result["checks"].values())
    )
    result["end_to_end_completed"] = (
        result["source_and_contract_passed"] and result["runtime_completed"]
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    selection = json.loads((FIXTURES / "selection.json").read_text())
    planned = selection["planned_comparison"]
    environments_path = FIXTURES / "environments-v5.json"
    environments = json.loads(environments_path.read_text())
    preflight = json.loads(args.preflight.read_text())
    if (
        preflight["selected_ids"] != selection["selected_ids"]
        or not preflight.get("all_selected_qualified")
        or preflight["environment_manifest_sha256"] != digest(environments_path)
    ):
        raise SystemExit("Every fixed oracle must qualify without substitutions")
    randomizer = random.Random(planned["order_seed"])
    plan = []
    for repetition in range(1, planned["repetitions"] + 1):
        for identity in selection["selected_ids"]:
            arms = list(planned["arms"])
            randomizer.shuffle(arms)
            plan.extend(
                {"case": identity, "repetition": repetition, "arm": arm} for arm in arms
            )
    if not args.live:
        print(
            json.dumps(
                {
                    "plan": plan,
                    "maximum_upstream_requests": len(plan) * CALLS,
                    "external_model_calls": 0,
                },
                ensure_ascii=False,
            )
        )
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if output.exists() or artifacts.exists() or output.suffix != ".json":
        raise SystemExit("Fresh report and artifact directory required")
    if (
        len(plan) * CALLS != MAXIMUM
        or planned["calls_per_arm"] != CALLS
        or planned["seconds_per_arm"] != SECONDS
    ):
        raise SystemExit("Preregistered budget drift")
    prior = (
        sum(
            json.loads(p.read_text()).get("attempted_calls", 0)
            for p in output.parent.rglob("requests.json")
        )
        if output.parent.exists()
        else 0
    )
    if prior + MAXIMUM > MAXIMUM:
        raise SystemExit(
            "Cumulative directory budget cannot reserve complete balanced plan"
        )
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    instances = {
        name: json.loads((FIXTURES / name / "instance.json").read_text())
        for name in selection["selected_ids"]
    }
    artifacts.mkdir(parents=True)
    report = {
        "kind": "paired-public-transfer/v1",
        "runtime_version": __version__,
        "scope": selection["scope"],
        "selection": selection,
        "preflight_sha256": digest(args.preflight),
        "environment_manifest_sha256": digest(environments_path),
        "model": settings["MEDAGENT_LLM_MODEL"],
        "plan": plan,
        "maximum_calls": MAXIMUM,
        "calls_per_arm": CALLS,
        "seconds_per_arm": SECONDS,
        "file_contract": CONTRACT,
        "prompts": {
            name: task_prompt(instance) for name, instance in instances.items()
        },
        "source_sha256": source_inventory(),
        "runs": [],
        "active": None,
        "attempted_calls": 0,
        "full_dsh_parity_proven": False,
        "clinical_validation_complete": False,
    }
    for name, sha in report["source_sha256"].items():
        source = ROOT / name
        if digest(source) != sha:
            raise ValueError("Source changed during freeze")
        target = artifacts / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def save():
        report["attempted_calls"] = sum(
            json.loads(p.read_text()).get("attempted_calls", 0)
            for p in artifacts.rglob("requests.json")
        )
        report["frozen_source_unchanged"] = (
            source_inventory() == report["source_sha256"]
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise ValueError("Credential found in report")
        temporary = output.with_suffix(".tmp")
        temporary.write_text(encoded + "\n")
        temporary.replace(output)

    save()
    try:
        for index, item in enumerate(plan):
            if source_inventory() != report["source_sha256"]:
                raise ValueError("Frozen source drift")
            if report["attempted_calls"] + (len(plan) - index) * CALLS > MAXIMUM:
                raise ValueError("Insufficient remaining hard budget")
            folder = artifacts / f"r{item['repetition']}-{item['case']}-{item['arm']}"
            report["active"] = {
                **item,
                "output": str(folder),
                "supervisor_pid": os.getpid(),
                "phase": "starting",
            }
            save()

            def progress(**values):
                if any(report["active"].get(k) != v for k, v in values.items()):
                    report["active"].update(values)
                    save()

            result = evaluate(
                item,
                environments["cases"][item["case"]],
                instances[item["case"]],
                preflight["cases"][item["case"]]["test_id_map"],
                settings,
                folder,
                report["prompts"][item["case"]],
                progress,
            )
            report["runs"].append(result)
            report["active"] = None
            save()
            print(
                json.dumps(
                    {
                        **item,
                        "implementation_passed": result["implementation_passed"],
                        "runtime_completed": result["runtime_completed"],
                        "added_regressions_passed": result.get(
                            "added_regressions", {}
                        ).get("passed"),
                        "end_to_end_completed": result["end_to_end_completed"],
                        "calls": result["gateway"]["attempted_calls"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        report["plan_complete"] = len(report["runs"]) == len(plan)
        save()
    finally:
        save()


if __name__ == "__main__":
    main()
