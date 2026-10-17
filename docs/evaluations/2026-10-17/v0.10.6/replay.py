"""Re-execute saved three-step conformance decisions with a frozen project, without API."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from decision_provider import LocalProvider, decisions


def verify_source(project):
    source = json.loads((HERE / "source-files.json").read_text())
    for name, expected in source.items():
        if (
            not (project / name).is_file()
            or hashlib.sha256((project / name).read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Frozen source drift: " + name)
    return len(source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project, output = args.project.resolve(), args.output.resolve()
    if output.exists():
        raise SystemExit("Fresh output required")
    count = verify_source(project)
    sys.path[:0] = [str(project / "src"), str(project / "scripts")]
    from evaluate_check_recovery import run_case

    original = json.loads((HERE / "real-provider/round1.json").read_text())["case"]
    frames = decisions(original)
    provider = LocalProvider(frames)
    try:
        with tempfile.TemporaryDirectory(prefix="check-inputs-replay-") as raw:
            result = run_case(
                {
                    "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                    "MEDAGENT_LLM_API_KEY": "local-saved-decisions-only",
                    "MEDAGENT_LLM_MODEL": "saved-decisions",
                },
                Path(raw) / "case",
            )
    finally:
        provider.shutdown()
        provider.server_close()
    checks = {
        "same_three_saved_decisions": len(provider.requests)
        == len(frames)
        == original["gateway"]["attempted_calls"]
        == 3,
        "same_complete_conformance": result["passed"] is original["passed"] is True,
        "same_check_exit_states": [v["exit_code"] for v in result["observations"]]
        == [v["exit_code"] for v in original["observations"]],
        "source_unchanged": verify_source(project) == count,
    }
    record = {
        "kind": "frozen-check-inputs-replay/v1",
        "external_model_calls": 0,
        "source_files_checked": count,
        "checks": checks,
        "passed": all(checks.values()),
        "case": result,
    }
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({k: v for k, v in record.items() if k != "case"}, ensure_ascii=False)
    )
    raise SystemExit(0 if record["passed"] else 1)


if __name__ == "__main__":
    main()
