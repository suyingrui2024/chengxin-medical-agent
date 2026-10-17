"""Execute the saved 20 public-task decisions with a restored environment and new controller."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from decision_provider import LocalProvider, decisions
from replay import verify_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.project.resolve(), args.output.resolve()
    artifacts = output.with_suffix("")
    if output.exists() or artifacts.exists():
        raise SystemExit("Fresh output required")
    count = verify_source(root)
    transfer = HERE.parent.parent / "2026-10-16/v0.10.5-transfer"
    frozen = json.loads((transfer / "source-files.json").read_text())
    for name, sha in frozen.items():
        if name.startswith("benchmarks/") or name.endswith("/candidate_tree.py"):
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != sha:
                raise SystemExit("Public task input drift: " + name)
    sys.path[:0] = [str(root / "src"), str(root / "scripts")]
    from benchmark_public_transfer import evaluate

    original = json.loads((transfer / "complete.json").read_text())
    name = "matplotlib__matplotlib-25498"
    row = next(
        r
        for r in original["runs"]
        if r["case"] == name and r["arm"] == "product" and r["repetition"] == 2
    )
    frames = decisions({"snapshot": row["snapshots"][0]})
    assert len(frames) == row["gateway"]["attempted_calls"] == 20
    assert not any(
        b.get("name") in {"subagent", "subagent_fork", "web_search", "web_fetch"}
        for frame in frames
        for b in frame
    )
    environment = json.loads(
        (root / "benchmarks/swe-lite-transfer/environments-v5.json").read_text()
    )["cases"][name]
    instance = json.loads(
        (root / "benchmarks/swe-lite-transfer" / name / "instance.json").read_text()
    )
    mapping = json.loads((transfer / "preflight.json").read_text())["cases"][name][
        "test_id_map"
    ]
    provider = LocalProvider(frames)
    try:
        result = evaluate(
            {"case": name, "repetition": 2, "arm": "product"},
            environment,
            instance,
            mapping,
            {
                "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                "MEDAGENT_LLM_API_KEY": "local-saved-decisions-only",
                "MEDAGENT_LLM_MODEL": "saved-decisions",
            },
            artifacts,
            original["prompts"][name],
        )
    finally:
        provider.shutdown()
        provider.server_close()
    checks = {
        "same_twenty_saved_decisions": len(provider.requests) == len(frames) == 20,
        "root_completed": result["runtime_completed"] is True,
        "implementation_passed": result["implementation_passed"] is True,
        "joint_delivery_passed": result["end_to_end_completed"] is True,
        "source_unchanged": verify_source(root) == count,
    }
    result.update(
        kind="frozen-public-decisions-counterfactual/v1",
        external_model_calls=0,
        source_files_checked=count,
        counterfactual_checks=checks,
        counterfactual_passed=all(checks.values()),
        scope="Saved decisions, not fresh model reasoning; original public scores remain unchanged.",
    )
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "checks": checks,
                "external_model_calls": 0,
                "local_requests": len(provider.requests),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
