"""Assemble release evidence only after the final test processes have terminated."""
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent
ARCHIVE = ROOT / "docs/evaluations/2026-10-17/v0.11.5"
assert not (ARCHIVE / "archive-manifest.json").exists()


def log(name, pattern):
    content = (ROOT / ".runtime" / name).read_text()
    assert re.search(pattern, content, re.M), name
    shutil.copy2(ROOT / ".runtime" / name, ARCHIVE / name)
    return name


logs = {
    "backend_default": log("repeated-observations-backend-release.log", r"^Ran 279 tests"),
    "sdk_isolated": log("repeated-observations-sdk.log", r"^Ran 50 tests"),
    "ui_complete": log("repeated-observations-ui-complete.log", r"37 passed"),
    "ui_final_risk_tone": log("repeated-observations-ui-risk-tone.log", r"2 passed"),
    "native_package_final": log("repeated-observations-native-final-package.log", r"24 passed"),
    "native_source_small_windows": log("repeated-observations-native-minimum-final.log", r"2 passed"),
    "format": log("repeated-observations-format-seal.log", r"All matched files use Prettier code style"),
}
assert "OK (skipped=53)" in (ARCHIVE / logs["backend_default"]).read_text()
assert re.search(r"^OK$", (ARCHIVE / logs["sdk_isolated"]).read_text(), re.M)
for name, source in {
    "package-audit": "package-final-audit",
    "frozen-verification": "frozen-final-artifact-verification",
}.items():
    for suffix in (".json", ".log"):
        shutil.copy2(WORK / (source + suffix), ARCHIVE / (name + suffix))
package = json.loads((ARCHIVE / "package-audit.json").read_text())
frozen = json.loads((ARCHIVE / "frozen-verification.json").read_text())
live = json.loads((ARCHIVE / "live.json").read_text())
previous = json.loads((ARCHIVE / "previous-archives-verification.json").read_text())
assert package["passed"] and frozen["passed"] and live["passed"] and previous["passed"]
assert live["attempted_calls"] == 3 and all(c["status"] == 200 for c in live["calls"])
assert frozen["tests_run"] == 83 and frozen["source_files_checked"] == 247
final = json.loads((ARCHIVE / "source-files.json").read_text())
before = json.loads((ARCHIVE / "source-before-live.json").read_text())
assert len(final) == 247 and len(before) == 246
for base, values in ((ROOT, final), (ARCHIVE / "snapshot-final", final), (ARCHIVE / "pre-live-source", before)):
    assert all((base / n).is_file() and hashlib.sha256((base / n).read_bytes()).hexdigest() == d for n, d in values.items())
assert all(before[n] == final[n] for n in before if n.startswith("src/medagent/") and "/web/" not in n)
for prefix in ("57-repeated", "58-repeated", "59-repeated", "60-repeated", "61-native-small"):
    for path in (ROOT / "docs/assets/ui").glob(prefix + "*.png"):
        shutil.copy2(path, ARCHIVE / "screenshots" / path.name)
shutil.copy2(__file__, ARCHIVE / "assemble_verification.py")
report = {
    "version": "0.11.5",
    "synthetic_only": True,
    "passed": True,
    "checks": {
        "default_backend": {"collected":279,"passed":226,"skipped":53},
        "separate_sdk_and_isolated": {"passed":50,"skipped":0},
        "unique_backend_passed":276,
        "long_running_not_rerun":3,
        "ui_complete_passed":37,
        "ui_after_final_risk_color_passed":2,
        "packaged_native_complete_passed":24,
        "source_native_scope":"Initial full22 passed. Expanded full23:22 passed and one small-window failure; layout corrected, actual900x760 and900x680 final targeted checks both passed. See all retained logs; do not call the failed expanded source run fully green.",
        "frozen_tests_passed":83,
        "source_files_checked":247,
        "own_packaged_files_checked":package["source_files_checked"],
        "applicable_production_dependency_versions_checked":package["locked_production_packages_checked"],
        "provider_calls":3,
        "repeat_flow_provider_calls":0,
        "provider_statuses":[c["status"] for c in live["calls"]],
        "pre_run_snapshot_matches":True,
        "final_snapshot_matches_current_sources":True,
        "post_live_python_runtime_prompt_and_packs_unchanged":True,
        "previous_sealed_archives_unchanged":len(previous["archives"]),
    },
    "logs":logs,
    "artifacts": ["README.md","source-files.json","source-before-live.json","post-live-changes.json","live.json","package-audit.json","frozen-verification.json","previous-archives-verification.json"],
    "scope":"Engineering and synthetic outpatient/desktop evaluation, not clinical validation or broad DSH/Codex capability parity. Final color follows existing controller risk, not a new clinical threshold.",
}
(ARCHIVE / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
for name in report["artifacts"]:
    assert (ARCHIVE / name).is_file(), name
missing = []
for name in ["README.md","docs/重复测量与问诊接续.md","docs/首版产品与技术设计.md","docs/实验与验收方案.md","docs/桌面应用使用与打包.md","docs/结构化展示协议与组件扩展.md","docs/能力插件与问诊模板.md"]:
    page = ROOT / name
    for raw in re.findall(r"!?\[[^\]]*\]\(([^\n)]+)\)", page.read_text()):
        dest = raw.split("#",1)[0]
        if dest and "://" not in dest and not (page.parent / dest).exists(): missing.append([name, raw])
for raw in re.findall(r"!?\[[^\]]*\]\(([^\n)]+)\)", (ARCHIVE / "README.md").read_text()):
    dest = raw.split("#",1)[0]
    if dest and "://" not in dest and not (ARCHIVE / dest).exists(): missing.append(["archive/README.md",raw])
assert not missing, missing
(ARCHIVE / "link-verification.json").write_text(json.dumps({"checked_current_documents":7,"archive_readme":True,"missing":missing,"passed":True},ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"verification_index_written":True,"source_files":247,"all_required_evidence_passed":True,"links_passed":True}))
