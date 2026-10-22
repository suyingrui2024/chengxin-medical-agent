"""Bounded synthetic real-model generation and independent file-projection conformance."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
import csv
from decimal import Decimal, InvalidOperation
import io
import json
from pathlib import Path
import shutil
import threading

from scripts.benchmarks.benchmark_execution import BoundedGateway, product_run
from scripts.evaluation.execution.evaluate_delivery_checks import fingerprints
from medagent import __version__
from medagent.env_config import read_env, for_backend
from medagent.execution import ExecutionManager

ROOT = Path(__file__).resolve().parents[3]
PROMPT = """这是非医疗合成产物验收，只操作当前工作区，不联网、不安装依赖、不启动子任务。输入样本A=1.25kg、B=2.00kg、C=0.75kg。
编写plot.py，用标准库Decimal按g=kg*1000计算并保存analysis.csv，首行恰为sample,kg,g，后续恰为A/B/C三行；再用Matplotlib将同一计算数据保存为analysis.png柱状图，至少400×240像素，横轴样本、纵轴Mass (g)，图内用英文以免中文字体缺失。不要生成诊疗结论。真正运行脚本，使用delivery_check做必要自检，观察实际结果后简短收尾。最多8次模型请求，不做无关工作。"""


def verify_rows(text):
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        return (
            len(rows) == 4
            and rows[0] == ["sample", "kg", "g"]
            and all(
                len(row) == 3
                and row[0] == name
                and Decimal(row[1]).is_finite()
                and Decimal(row[2]).is_finite()
                and Decimal(row[1]) == kg
                and Decimal(row[2]) == kg * 1000
                for row, (name, kg) in zip(
                    rows[1:],
                    [
                        ("A", Decimal("1.25")),
                        ("B", Decimal("2.00")),
                        ("C", Decimal("0.75")),
                    ],
                )
            )
        )
    except (csv.Error, InvalidOperation, ValueError):
        return False


def run_case(settings, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    gateway = BoundedGateway(settings, 8, output / "requests.json")
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    manager = ExecutionManager(root=output / "workspaces")
    session = None
    result = {"kind": "artifact-preview-conformance/v1", "prompt": PROMPT, "checks": {}}
    try:
        session = manager.create(
            {
                **settings,
                "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
                "MEDAGENT_LLM_API_KEY": gateway.token,
            }
        )
        snapshot = product_run(session, PROMPT, 8)
        result["snapshot"] = snapshot
        result["checks"]["root_completed"] = snapshot["status"] == "completed"
        result["checks"]["actual_accepted_check_passed"] = any(
            v["tool"] == "delivery_check"
            and v.get("exit_code") == 0
            and v.get("check_key")
            for v in session.observations
        )
    except Exception as error:
        result["checks"]["execution_finished"] = False
        result["error_type"] = type(error).__name__
    finally:
        manager.close()
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        result["gateway"] = gateway.metrics()
    if session:
        try:
            table = session.preview_file("analysis.csv")
            image = session.preview_file("analysis.png")
            data, mime = session.image_file("analysis.png", image["sha256"])
            result["table"] = table
            result["image"] = image
            result["checks"].update(
                independent_numeric_rows=verify_rows(table["text"]),
                actual_table_projection=table["kind"] == "table"
                and len(table["rows"]) == 4,
                actual_static_png=image["kind"] == "image"
                and mime == "image/png"
                and image["width"] >= 400
                and image["height"] >= 240,
                image_hash_transport_matches=session.artifact_status(
                    "analysis.png", image["sha256"]
                )["matches"],
            )
            destination = output / "candidate"
            destination.mkdir()
            (destination / "analysis.png").write_bytes(data)
            for name in ["analysis.csv", "plot.py"]:
                (destination / name).write_text(
                    session.read_file(name)["text"], encoding="utf-8"
                )
            result["checks"]["actual_source_saved"] = (
                destination / "plot.py"
            ).stat().st_size > 0
            restored = ExecutionManager(root=output / "workspaces")
            try:
                cold = restored.open(session.id, {})
                result["checks"]["offline_projection_matches"] = (
                    cold.preview_file("analysis.csv") == table
                    and cold.preview_file("analysis.png") == image
                    and cold.process is None
                )
            finally:
                restored.close()
        except Exception as error:
            result["checks"]["artifact_verification_completed"] = False
            result["verification_error_type"] = type(error).__name__
    result["checks"]["bounded_requests"] = len(gateway.calls) <= 8
    result["checks"]["all_http200"] = bool(gateway.calls) and all(
        c["status"] == 200 for c in gateway.calls
    )
    result["passed"] = all(result["checks"].values())
    result["image_semantics_automatically_verified"] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "planned_cases": 1,
                    "maximum_external_calls": 8,
                    "external_model_requests": 0,
                }
            )
        )
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if output.exists() or artifacts.exists():
        raise SystemExit("Fresh output required")
    source = fingerprints()
    for name in source:
        target = artifacts / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    report = {"version": __version__, "source_sha256": source, "case": None}
    try:
        report["case"] = run_case(
            for_backend(read_env(ROOT / ".env"), "dsh"), artifacts / "case"
        )
    finally:
        report["source_unchanged_during_calls"] = fingerprints() == source
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["case"]["passed"],
                "actual_requests": report["case"]["gateway"]["attempted_calls"],
                "checks": report["case"]["checks"],
                "source_unchanged_during_calls": report[
                    "source_unchanged_during_calls"
                ],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(
        0 if report["case"]["passed"] and report["source_unchanged_during_calls"] else 1
    )


if __name__ == "__main__":
    main()
