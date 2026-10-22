"""Collect and verify every preregistered task, retaining failures without replacement."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import json
from pathlib import Path
import shutil
import hashlib

from scripts.benchmarks.public.public_test_sample import (
    ROOT,
    FIXTURES,
    inventory,
    run_code,
    code_for,
    parse,
    resolve_ids,
    classify,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--environments", type=Path, default=FIXTURES / "environments-v5.json"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Use a fresh preflight output directory")
    output.mkdir(parents=True)
    selection = json.loads((FIXTURES / "selection.json").read_text())
    environments = json.loads(args.environments.read_text())
    report = {
        "kind": "public-test-preflight/v1",
        "environment_manifest_sha256": hashlib.sha256(
            args.environments.read_bytes()
        ).hexdigest(),
        "selected_ids": selection["selected_ids"],
        "replacements": [],
        "model_calls": 0,
        "cases": {},
    }

    def save():
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    save()
    for identity in selection["selected_ids"]:
        entry = {}
        report["cases"][identity] = entry
        try:
            instance = json.loads((FIXTURES / identity / "instance.json").read_text())
            environment = environments["cases"][identity]
            source = ROOT / environment["prepared_source"]
            if inventory(source) != environment["source_sha256"]:
                raise ValueError("Prepared source/dependency drift")
            folder = output / identity
            folder.mkdir()
            collect = folder / "collect"
            shutil.copytree(source, collect)
            result = parse(
                run_code(
                    collect, folder / "collect-state", code_for(instance, "collect")
                )
            )
            entry["collection"] = result
            save()
            if (
                result.get("setup_error")
                or not result.get("runtime_completed")
                or result.get("pytest_exit_code") != 0
            ):
                raise ValueError("Collection preflight failed")
            mapping = resolve_ids(instance, result["collected"])
            entry["test_id_map"] = mapping
            for phase in ["original", "reference"]:
                workspace = folder / phase
                shutil.copytree(source, workspace)
                result = classify(
                    parse(
                        run_code(
                            workspace,
                            folder / (phase + "-state"),
                            code_for(instance, phase, mapping),
                        )
                    )
                )
                entry[phase] = result
                save()
            entry["qualified"] = entry["original"].get(
                "original_bug_reproduced", False
            ) and entry["reference"].get("passed", False)
        except (ValueError, KeyError, OSError, TimeoutError) as error:
            entry["qualified"] = False
            entry["setup_error"] = type(error).__name__
            entry["diagnostic"] = str(error)[:1500]
        save()
        print(
            json.dumps(
                {
                    "case": identity,
                    "qualified": entry["qualified"],
                    "setup_error": entry.get("setup_error"),
                    "original_bug_reproduced": entry.get("original", {}).get(
                        "original_bug_reproduced"
                    ),
                    "reference_passed": entry.get("reference", {}).get("passed"),
                }
            ),
            flush=True,
        )
    report["all_six_qualified"] = all(
        entry.get("qualified") for entry in report["cases"].values()
    )
    save()
    raise SystemExit(0 if report["all_six_qualified"] else 1)


if __name__ == "__main__":
    main()
