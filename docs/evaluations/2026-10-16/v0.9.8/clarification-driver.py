"""Known DEV clarification probe, separately reported within the existing call cap."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
import benchmark_public_execution as execution
from execution_task_contract import compliance, prompt_for
from medagent.env_config import for_backend, read_env
from public_execution_cases import load_cases, sha256


def main():
    parent = ROOT / ".runtime/evaluations/public-contract-v098"
    original = json.loads((parent / "report.json").read_text())
    if not original.get("all_planned_pairs_completed"):
        raise SystemExit("Wait for the frozen paired experiment to finish")
    for name, expected in original["source_sha256"].items():
        if sha256((ROOT / name).read_bytes()) != expected:
            raise SystemExit("Frozen source changed: " + name)
    spent = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in parent.rglob("requests.json")
    )
    if spent + 24 > original["maximum_calls"]:
        raise SystemExit(
            "Insufficient remaining cumulative budget for both 12-call arms"
        )
    output = parent / "clarified-sql.json"
    artifacts = output.with_suffix("")
    if output.exists() or artifacts.exists():
        raise SystemExit("Probe output already exists; do not silently rerun")
    artifacts.mkdir()
    _, cases = load_cases(ROOT / original["fixtures"])
    case = deepcopy(cases["sqlfluff__sqlfluff-1625"])
    clarification = """

维护需求澄清：原 issue 的“单表查询应当通过”不作为本次修改目标。维护者明确保留 L031 对 FROM 别名及 JOIN 别名的既有判定和自动修复行为。
本次只纠正对外规则描述与告警措辞，使告警文本为：Avoid aliases in from clauses and join conditions.
需要使规则说明与该范围一致；不能通过取消单表告警、禁用规则或改变默认配置解决。
原测试仍严格逐字保护，其中旧 CLI 文案断言可能因此失败；这是已知的旧期望差异，不要改写原测试，也不要回滚正确的新文案去迎合旧断言。
可在 regressions/test_*.py 补充新文案和行为保持测试，实际运行并如实说明验证情况。本轮不要求下载上游代码或参考补丁。
"""
    case["metadata"]["problem_statement"] += clarification
    prompt = prompt_for(case)
    report = {
        "kind": "known-dev-clarification-probe/v1",
        "scope": "One known DEV task, one pair, changed requirements and 12 calls per arm. Not a new holdout, causal attribution or replacement score.",
        "parent_report_sha256": sha256((parent / "report.json").read_bytes()),
        "calls_before_probe": spent,
        "cumulative_maximum_calls": original["maximum_calls"],
        "maximum_calls_per_arm": 12,
        "prompt": prompt,
        "prompt_sha256": sha256(prompt.encode()),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_sha256": original["source_sha256"],
        "runs": [],
    }
    (artifacts / "driver.py").write_bytes(Path(__file__).read_bytes())
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    if settings["MEDAGENT_LLM_MODEL"] != original["model"]:
        raise SystemExit("Configured model changed")
    report["model"] = settings["MEDAGENT_LLM_MODEL"]
    execution.CALLS_PER_ARM = (
        12  # Same external gateway and per-turn limits for both paths.
    )

    def save():
        report["cumulative_attempted_calls"] = sum(
            json.loads(p.read_text()).get("attempted_calls", 0)
            for p in parent.rglob("requests.json")
        )
        report["source_unchanged"] = all(
            sha256((ROOT / name).read_bytes()) == expected
            for name, expected in report["source_sha256"].items()
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Refusing credential in report")
        output.write_text(encoded + "\n")

    save()
    try:
        for arm in ["official-sdk", "product"]:
            record = execution.evaluate(
                arm,
                case,
                settings,
                artifacts / arm,
                prompt=prompt,
                source_check=compliance,
            )
            record.update(
                case="sqlfluff__sqlfluff-1625",
                repetition=1,
                artifact_directory=str((artifacts / arm).relative_to(ROOT)),
            )
            report["runs"].append(record)
            save()
            print(
                json.dumps(
                    {
                        "arm": arm,
                        "passed": record["passed"],
                        "source_passed": record.get("independent_verification", {}).get(
                            "passed"
                        ),
                        "calls": record["gateway"]["attempted_calls"],
                        "cumulative_calls": report["cumulative_attempted_calls"],
                    }
                ),
                flush=True,
            )
        report["both_arms_completed"] = True
    finally:
        save()


if __name__ == "__main__":
    main()
