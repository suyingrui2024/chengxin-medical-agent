"""Execute saved public-task decisions under the patched controller, without a model."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
spec = importlib.util.spec_from_file_location(
    "saved_decision_provider",
    ROOT / "docs/evaluations/2026-10-16/v0.10.5/replay.py",
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
from benchmark_public_transfer import evaluate
from evaluate_delivery_checks import fingerprints

report = json.loads((ROOT / ".runtime/evaluations/transfer-v0105/round1.json").read_text())
name = "matplotlib__matplotlib-25498"
row = next(r for r in report["runs"] if r["case"] == name and r["arm"] == "product" and r["repetition"] == 2)
frames = helper.decisions({"snapshot": row["snapshots"][0]})
assert len(frames) == row["gateway"]["attempted_calls"] == 20
assert not any(b.get("name") in {"subagent", "subagent_fork", "web_search", "web_fetch"} for frame in frames for b in frame)
frozen = fingerprints()
output = ROOT / ".runtime/evaluations/recovery-v0106/matplotlib-saved-decisions"
provider = helper.LocalProvider(frames)
try:
    environment = json.loads((ROOT / "benchmarks/swe-lite-transfer/environments-v5.json").read_text())["cases"][name]
    instance = json.loads((ROOT / "benchmarks/swe-lite-transfer" / name / "instance.json").read_text())
    mapping = json.loads((ROOT / ".runtime/public-transfer/preflight-01/report.json").read_text())["cases"][name]["test_id_map"]
    result = evaluate(
        {"case": name, "repetition": 2, "arm": "product"}, environment, instance, mapping,
        {"MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic", "MEDAGENT_LLM_API_KEY": "local-saved-decisions-only", "MEDAGENT_LLM_MODEL": "saved-decisions"},
        output, report["prompts"][name],
    )
finally:
    provider.shutdown()
    provider.server_close()
result.update(
    scope="Same saved root decisions executed with the new controller and original independent oracles; no fresh model decisions, no revised historical public score.",
    external_model_calls=0,
    original_runtime_completed=row["runtime_completed"],
    original_error=row["snapshots"][0].get("error"),
    source_sha256=frozen,
    source_unchanged=fingerprints() == frozen,
)
checks = {
    "same_20_decisions": len(provider.requests) == len(frames) == 20,
    "root_completed": result["runtime_completed"] is True,
    "implementation_passed": result["implementation_passed"] is True,
    "new_regressions_passed": result["added_regressions"]["passed"] is True,
    "source_unchanged": result["source_unchanged"],
}
result["counterfactual_checks"] = checks
result["counterfactual_passed"] = all(checks.values())
(ROOT / ".runtime/v0106-matplotlib-counterfactual.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"checks": checks, "external_model_calls": 0, "local_requests": len(provider.requests), "end_to_end_completed": result["end_to_end_completed"]}, ensure_ascii=False))
raise SystemExit(0 if all(checks.values()) else 1)
