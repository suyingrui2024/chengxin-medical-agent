"""Re-execute both saved decision sequences with the final source and local provider."""
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


def verify(project):
    source = json.loads((HERE / "source-files.json").read_text())
    for name, sha in source.items():
        if (
            not (project / name).is_file()
            or hashlib.sha256((project / name).read_bytes()).hexdigest() != sha
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
    count = verify(project)
    sys.path[:0] = [str(project / "src"), str(project / "scripts")]
    from evaluate_artifact_preview import run_case

    result = {
        "kind": "frozen-artifact-decisions-replay/v1",
        "external_model_calls": 0,
        "source_files_checked": count,
        "rounds": [],
        "scope": "Saved decisions run under final code. Round 1 originally used an earlier persona; this is not a new model evaluation or an exact reproduction of its entire original runtime.",
    }
    for number in [1, 2]:
        original = json.loads(
            (HERE / "real-provider" / f"round{number}.json").read_text()
        )["case"]
        frames = decisions(original)
        provider = LocalProvider(frames)
        try:
            with tempfile.TemporaryDirectory(
                prefix="artifact-decisions-replay-"
            ) as raw:
                replay = run_case(
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
            "same_decision_count": len(provider.requests)
            == len(frames)
            == original["gateway"]["attempted_calls"],
            "same_conformance_verdict": replay["passed"] is original["passed"],
            "same_numeric_and_image_checks": all(
                replay["checks"].get(name) is original["checks"].get(name) is True
                for name in [
                    "independent_numeric_rows",
                    "actual_table_projection",
                    "actual_static_png",
                    "offline_projection_matches",
                ]
            ),
            "same_root_status": replay["snapshot"]["status"]
            == original["snapshot"]["status"],
            "source_unchanged": verify(project) == count,
        }
        result["rounds"].append(
            {
                "round": number,
                "checks": checks,
                "passed": all(checks.values()),
                "case": replay,
            }
        )
        print(
            json.dumps(
                {
                    "round": number,
                    "checks": checks,
                    "local_requests": len(provider.requests),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        result["passed"] = len(result["rounds"]) == 2 and all(
            r["passed"] for r in result["rounds"]
        )
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
