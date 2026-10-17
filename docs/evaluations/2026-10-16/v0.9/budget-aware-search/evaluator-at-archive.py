"""Bounded real-provider evaluation of the full execution runtime, synthetic only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from medagent.env_config import read_env
from medagent.execution import ExecutionManager, safe_file


CODING = """这是合成工程验收，不是患者请求。请真正创建并执行文件：
1. 编写纯 Python convert.py，提供 convert(value, from_unit, to_unit) -> str，使用 Decimal 精确换算 kg/g/mg。拒绝负数和不支持的单位。返回去掉无意义小数零的十进制字符串。
2. 编写 test_convert.py 并运行 unittest，保存完整结果至 tests-output.txt。测试包含 1.25 kg→g=1250、0.1 g→mg=100、1234 mg→g=1.234、0 g→mg=0，以及非法单位与负数。
3. 保存 result.json，字段 results 恰为 ["1250", "100", "1.234", "0"]，这些值必须来自实际执行上述函数。
4. 使用 subagent 创建独立子智能体，读取代码并执行复核；让它保存 review.txt，说明实际检查及发现的问题。你根据问题修复并再次运行测试。
5. 检查文件后简要报告；不要声称完成未实际执行的测试。"""
FOLLOWUP = '保留现有文件和同一会话，调用已写好的 convert 将 1000 mg 换算为 g，实际执行并保存 continuation.json，内容为 {"result":"1"}。'
SEARCH = """这是非医疗的合成检索验收。请调用 web_search 搜索 Python Decimal 的官方文档，再用 web_fetch 打开官方文档页面。保存 sources.json，字段 sources 为包含 url、title 的数组，并保存 research.txt，用简短中文解释 Decimal 如何避免二进制浮点表示带来的部分误差，附实际查阅的官方链接。如果工具或网络失败，明确记录失败，不要伪造已查阅的来源。"""
PROBE = """import importlib.util,json
spec=importlib.util.spec_from_file_location('candidate','/workspace/convert.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for value,a,b,expected in [('1.25','kg','g','1250'),('0.1','g','mg','100'),('1234','mg','g','1.234'),('0','g','mg','0'),('0.0025','kg','mg','2500'),('123456789.123','mg','g','123456.789123')]:
 assert m.convert(value,a,b)==expected,(value,a,b,m.convert(value,a,b),expected)
for args in [('-1','kg','g'),('1','bad','g')]:
 try:m.convert(*args)
 except ValueError:pass
 else:raise AssertionError('invalid input accepted')
assert json.load(open('/workspace/result.json'))['results']==['1250','100','1.234','0']
print('independent hidden checks passed')
"""
REPAIR = """这是合成工程修复任务，工作区已有上一轮真实生成的 convert.py、test_convert.py、result.json、review.txt。不要重新实现无关项目。
独立检查发现：convert('12345678901234567890123456789.123','mg','g') 得到了 '12345678901234567890123456.79'，正确值是 '12345678901234567890123456.789123'。使用 Decimal 并不自动避免上下文舍入。
请先运行并重现，检查乘除、格式化及 normalize 等步骤的上下文影响，修复全流程，不能硬编码这两个例子；保留原来正常用例与期望值，新增超过28位有效数字、极小小数、以及调用方 Decimal 精度被设为8时仍精确的测试，不改变调用方全局精度。明确输入范围和拒绝超范围行为。仅使用标准库。
还需验证 convert('0.000000000000000000000000000001','kg','mg') == '0.000000000000000000000001'。
实际运行测试并保存 tests-output.txt，重新生成 result.json 的原四项结果。让 subagent 独立复核本次问题和正常用例，使用 run_in_background=false，保存新的 precision-review.txt。按当前任务和预算收尾，不扩大为无限范围审计。"""
STRONG_PROBE = (
    PROBE
    + """
from decimal import getcontext
saved=getcontext().prec
getcontext().prec=8
try:
 for value,a,b,expected in [('12345678901234567890123456789.123','mg','g','12345678901234567890123456.789123'),('0.000000000000000000000000000001','kg','mg','0.000000000000000000000001'),('1.23456789012345678901234567890123456789','kg','mg','1234567.89012345678901234567890123456789')]:
  assert m.convert(value,a,b)==expected,(value,m.convert(value,a,b),expected)
 assert getcontext().prec==8,'caller Decimal context was modified'
finally:getcontext().prec=saved
print('expanded precision checks passed')
"""
)


def run(session, prompt, maximum):
    session.start_turn(prompt, maximum)
    deadline = time.monotonic() + 215
    while time.monotonic() < deadline:
        snapshot = session.snapshot()
        if snapshot["status"] not in {"starting", "running", "cancelling"}:
            return snapshot
        time.sleep(0.2)
    session.cancel()
    raise RuntimeError("Execution evaluation deadline exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", default="coding,search")
    parser.add_argument("--seed-workspace", type=Path)
    parser.add_argument("--max-total-calls", type=int, default=60)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.max_total_calls <= 100:
        raise SystemExit(
            "Use a fresh report path and a total call bound between 1 and 100"
        )
    root = Path(__file__).resolve().parents[1]
    settings = read_env(root / ".env")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    spent = 0
    for path in output.parent.glob("*.json"):
        try:
            prior = json.loads(path.read_text())
            if prior.get("kind") == "full-dsh-execution-evaluation/v1":
                spent += int(prior.get("attempted_calls", 0))
        except (ValueError, OSError):
            pass
    if spent >= args.max_total_calls:
        raise SystemExit("Cumulative evaluation budget exhausted")
    manager = ExecutionManager(
        root=output.with_suffix("") / "workspaces", source_root=root
    )
    report = {
        "synthetic_only": True,
        "kind": "full-dsh-execution-evaluation/v1",
        "cases": [],
        "maximum_calls": args.max_total_calls,
        "attempted_calls": 0,
    }

    def remaining(cap):
        result = min(cap, args.max_total_calls - spent - report["attempted_calls"])
        if result < 1:
            raise RuntimeError("Evaluation budget exhausted")
        return result

    try:
        for name in args.cases.split(","):
            if name not in {"coding", "search", "repair"}:
                raise ValueError("Unknown case")
            session = manager.create(settings)
            if name == "repair":
                if not args.seed_workspace:
                    raise ValueError("Repair requires seed workspace")
                for filename in [
                    "convert.py",
                    "test_convert.py",
                    "result.json",
                    "review.txt",
                ]:
                    content = safe_file(args.seed_workspace.resolve(), filename)
                    (session.directory / filename).write_text(content["text"])
            case = {"id": name, "checks": {}, "snapshots": []}
            report["cases"].append(case)
            snapshot = run(
                session,
                {"coding": CODING, "search": SEARCH, "repair": REPAIR}[name],
                remaining({"coding": 40, "search": 16, "repair": 24}[name]),
            )
            report["attempted_calls"] += snapshot["turns"][-1].get("model_calls", 0)
            case["snapshots"].append(snapshot)
            case["checks"]["runtime_completed"] = snapshot["status"] == "completed"
            if name in {"coding", "repair"}:
                check = subprocess.run(
                    ["node", str(root / "integrations/dsh/check-artifacts.mjs")],
                    input=json.dumps(
                        {
                            "workspace": str(session.directory),
                            "state": str(session.state_directory),
                            "code": STRONG_PROBE if name == "repair" else PROBE,
                        }
                    ),
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                case["independent_check"] = (
                    json.loads(check.stdout)
                    if check.stdout.strip()
                    else {"passed": False}
                )
                case["checks"]["independent_hidden_cases"] = case[
                    "independent_check"
                ].get("passed", False)
                case["checks"]["subagent_actually_started"] = any(
                    event.get("notification", {}).get("method") == "subagent.started"
                    for event in snapshot["events"]
                )
                case["checks"]["review_artifact"] = (
                    session.directory
                    / ("precision-review.txt" if name == "repair" else "review.txt")
                ).is_file()
                if case["checks"]["independent_hidden_cases"]:
                    before = session.process
                    continued = run(session, FOLLOWUP, remaining(12))
                    report["attempted_calls"] += continued["turns"][-1].get(
                        "model_calls", 0
                    )
                    case["snapshots"].append(continued)
                    case["checks"]["same_runtime_continued"] = (
                        session.process is before and continued["status"] == "completed"
                    )
                    try:
                        case["checks"]["followup_result"] = (
                            json.loads(
                                (session.directory / "continuation.json").read_text()
                            )["result"]
                            == "1"
                        )
                    except (OSError, ValueError, KeyError):
                        case["checks"]["followup_result"] = False
            else:
                calls = [
                    event["notification"]["params"]["event"]["data"]["name"]
                    for event in snapshot["events"]
                    if event.get("notification", {}).get("method") == "session.event"
                    and event["notification"]["params"]["event"]["type"] == "tool/call"
                ]
                case["checks"]["search_and_fetch_called"] = (
                    "web_search" in calls and "web_fetch" in calls
                )
                try:
                    sources = json.loads(
                        (session.directory / "sources.json").read_text()
                    )["sources"]
                    case["checks"]["official_source_saved"] = any(
                        source["url"].startswith("https://docs.python.org/")
                        for source in sources
                    )
                except (OSError, ValueError, KeyError, TypeError):
                    case["checks"]["official_source_saved"] = False
                results = [
                    event["notification"]["params"]["event"]["data"]
                    for event in snapshot["events"]
                    if event.get("notification", {}).get("method") == "session.event"
                    and event["notification"]["params"]["event"]["type"]
                    == "tool/result"
                ]
                case["checks"]["no_tool_errors"] = not any(
                    result.get("message", {}).get("isError") for result in results
                )
            case["passed"] = all(case["checks"].values())
            print(
                json.dumps(
                    {
                        "case": name,
                        "passed": case["passed"],
                        "checks": case["checks"],
                        "total_calls": report["attempted_calls"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        manager.close()
        report["passed"] = bool(report["cases"]) and all(
            case.get("passed", False) for case in report["cases"]
        )
        report["source_sha256"] = {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for directory in [root / "src/medagent", root / "integrations/dsh"]
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix
            in {".py", ".mjs", ".js", ".yml", ".css", ".json", ".html", ".svg"}
        }
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Credential detected; refusing to write report")
        output.write_text(encoded + "\n")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
