"""Score capability selection: did the model pick the right capability?

The production prompt hands the model a compact catalog (id, name,
size, aliases) and nothing else. This harness reproduces exactly that
catalog, pairs it with a fixed case set, and scores a recorded run.

Two modes:

    --emit-prompt   write the prompt a model should answer (for a real
                    provider, or for a sub-agent standing in for one)
    --baseline      score a recorded answer file

A baseline is any JSON shaped like::

    {"model": "name", "cases": [{"id": "sel-01", "predicted": "calculate.gds15"}]}

`predicted: null` means the model declined to call anything. The harness
never calls a provider itself: that keeps the measurement reproducible
and keeps the case set out of any model's training loop by accident.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from medagent.agent import _capability_advertisement
from medagent.extensions.builtin import load_registry

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "tests" / "fixtures" / "capability_selection_cases.json"

INSTRUCTION = """你是门诊问诊系统的决策模块。下面是当前可用的计算能力目录，以及一位患者刚说的一句话。
请判断：患者是否在**明确请求**使用其中某一项？

规则：
- 只有患者明确要求做某项量表或算某个指标时才选择，问定义、问正常值、问怎么办都不算。
- 只能从目录里选 id；没有合适的就返回 null。
- 只输出一个 JSON 对象，不要解释。

输出格式：{"id": "calculate.xxx"} 或 {"id": null}
"""


def catalog(registry):
    return [
        _capability_advertisement(item)
        for item in registry.descriptions()
        if item.get("patient_form")
    ]


def emit_prompt(destination: Path):
    registry = load_registry()
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    payload = {
        "instruction": INSTRUCTION,
        "catalog": catalog(registry),
        "cases": [{"id": case["id"], "utterance": case["utterance"]} for case in cases],
        "answer_format": '{"answers": [{"id": "sel-01", "id_chosen": "calculate.gds15"}]}',
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total = sum(len(json.dumps(item, ensure_ascii=False)) for item in payload["catalog"])
    print(f"prompt written to {destination}")
    print(f"catalog entries: {len(payload['catalog'])}, bytes: {total}")
    print(f"cases: {len(cases)}")


def score(baseline_path: Path):
    registry = load_registry()
    known = {item["id"] for item in registry.descriptions()}
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    expected = {case["id"]: case["expected"] for case in cases}
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    predicted = {}
    for answer in baseline.get("cases", []):
        predicted[answer["id"]] = answer.get("id_chosen")

    buckets = {"correct": [], "wrong_id": [], "missed": [], "false_trigger": [], "unknown": []}
    for case in cases:
        want = expected[case["id"]]
        got = predicted.get(case["id"], "<missing>")
        if got == "<missing>":
            buckets["unknown"].append((case["id"], case["utterance"], want, got))
        elif got == want:
            buckets["correct"].append((case["id"], case["utterance"], want, got))
        elif got is None:
            buckets["missed"].append((case["id"], case["utterance"], want, got))
        elif want is None:
            buckets["false_trigger"].append((case["id"], case["utterance"], want, got))
        elif got not in known:
            buckets["unknown"].append((case["id"], case["utterance"], want, got))
        else:
            buckets["wrong_id"].append((case["id"], case["utterance"], want, got))

    total = len(cases)
    print(f"model: {baseline.get('model', '?')}")
    print(f"cases: {total}")
    for name in ("correct", "wrong_id", "missed", "false_trigger", "unknown"):
        count = len(buckets[name])
        print(f"  {name:14} {count:3}  {count / total:6.1%}")
    print(f"  {'accuracy':14} {len(buckets['correct']):3}  {len(buckets['correct']) / total:6.1%}")

    for name in ("wrong_id", "missed", "false_trigger", "unknown"):
        if not buckets[name]:
            continue
        print(f"\n{name}:")
        for case_id, utterance, want, got in buckets[name]:
            print(f"  {case_id}  {utterance!r:38} want={want} got={got}")

    return 0 if len(buckets["correct"]) == total else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-prompt", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    if args.emit_prompt:
        emit_prompt(args.emit_prompt)
        return 0
    if args.baseline:
        return score(args.baseline)
    parser.error("需要 --emit-prompt 或 --baseline")


if __name__ == "__main__":
    raise SystemExit(main())
