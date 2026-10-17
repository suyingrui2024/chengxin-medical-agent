"""Qualify all fixed transfer oracles without retaining large disposable worktrees."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from public_test_sample import (
    ROOT,
    inventory,
    run_code,
    code_for,
    parse,
    resolve_ids,
    classify,
)

FIXTURES = ROOT / "benchmarks/swe-lite-transfer"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Fresh preflight output required")
    output.mkdir(parents=True)
    selection = json.loads((FIXTURES / "selection.json").read_text())
    environments = FIXTURES / "environments-v5.json"
    manifest = json.loads(environments.read_text())
    report = {
        "kind": "public-transfer-preflight/v1",
        "selected_ids": selection["selected_ids"],
        "selection_sha256": hashlib.sha256(
            (FIXTURES / "selection.json").read_bytes()
        ).hexdigest(),
        "environment_manifest_sha256": hashlib.sha256(
            environments.read_bytes()
        ).hexdigest(),
        "replacements": [],
        "external_model_calls": 0,
        "cases": {},
    }

    def save():
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )

    save()
    for identity in selection["selected_ids"]:
        result = {}
        report["cases"][identity] = result
        try:
            instance = json.loads((FIXTURES / identity / "instance.json").read_text())
            environment = manifest["cases"][identity]
            source = ROOT / environment["prepared_source"]
            if inventory(source) != environment["source_sha256"]:
                raise ValueError("Prepared source drift")
            mapping = None
            for phase in ["collect", "original", "reference"]:
                with tempfile.TemporaryDirectory(
                    prefix=identity + "-" + phase + "-", dir=output
                ) as raw:
                    temporary = Path(raw)
                    workspace = temporary / "workspace"
                    shutil.copytree(source, workspace)
                    value = parse(
                        run_code(
                            workspace,
                            temporary / "state",
                            code_for(instance, phase, mapping),
                        )
                    )
                    if phase == "collect":
                        result["collection"] = value
                        save()
                        if (
                            value.get("setup_error")
                            or not value.get("runtime_completed")
                            or value.get("pytest_exit_code") != 0
                        ):
                            raise ValueError("Collection preflight failed")
                        mapping = resolve_ids(instance, value["collected"])
                        result["test_id_map"] = mapping
                    else:
                        result[phase] = classify(value)
                    save()
            result["qualified"] = result["original"].get(
                "original_bug_reproduced", False
            ) and result["reference"].get("passed", False)
        except (
            ValueError,
            KeyError,
            OSError,
            TimeoutError,
            subprocess.TimeoutExpired,
        ) as error:
            result.update(
                qualified=False,
                error_type=type(error).__name__,
                diagnostic=str(error)[:1000],
            )
        save()
        print(
            json.dumps(
                {
                    "case": identity,
                    "qualified": result["qualified"],
                    "error_type": result.get("error_type"),
                    "original_bug_reproduced": result.get("original", {}).get(
                        "original_bug_reproduced"
                    ),
                    "reference_passed": result.get("reference", {}).get("passed"),
                }
            ),
            flush=True,
        )
    report["all_selected_qualified"] = all(
        v.get("qualified") for v in report["cases"].values()
    ) and len(report["cases"]) == len(selection["selected_ids"])
    save()
    raise SystemExit(0 if report["all_selected_qualified"] else 1)


if __name__ == "__main__":
    main()
