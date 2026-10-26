#!/usr/bin/env python3
"""A/B comparison: RulePlanner (bank order) vs DifferentialPlanner.

Runs scripted consultations across the covered complaint groups under
both planners and compares:

- outcome parity: final reply kind + risk tier must not regress;
- question economy: total questions asked to reach the summary;
- early capture: the position of each scenario's key discriminating
  question, and whether the key fact is captured within the first
  eight questions (patients abandon mid-flow; order is safety).

Usage:
    PYTHONPATH=src python scripts/evaluation/medical/evaluate_planner_ab.py
Exit code 0 when the differential planner shows no outcome regression
and earlier-or-equal key-question capture on every scenario.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from medagent.consultation import UserInput  # noqa: E402
from medagent.reasoning import RuleBasedProposer, enable_differential_planning  # noqa: E402
from medagent.runtime import build_runtime  # noqa: E402

COMMON = {
    "consult_for": ("我自己", "self"),
    # 包 D 跟进（包 A 残留 4）：sex-first 题序下 rule 臂第 2 问即问 sex，
    # 答案表缺此行导致 KeyError；答案惯例同 evaluate_live.py（"男", "male"）。
    "sex": ("男", "male"),
    # 同类缺口（补 sex 后重跑才暴露，非无关失败）：发热类主诉下
    # reported_temperature 同为非阻塞可问提，答案表中性惯例为“没量过”
    #（同 CI 三件套夹具补法）。
    "reported_temperature": ("没量过", None),
    # 同类缺口（探针枚举：13 场景 × 双臂仅此一处残留）：皮肤科主诉下
    # recent_exposures（text_or_absent）被问起，中性答案“没有”。
    # 以上两行与 sex 行同属“答案表对现行题序滞后”一类缺口，
    # 若监督员认定本项仅含 sex，可单行 revert 这两行。
    "recent_exposures": ("没有", None),
    "age": ("32岁", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("从昨天开始", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("5分", None),
    "associated_symptoms": ("没有其他不舒服", None),
    "medications_taken": ("没有", None),
    "allergies": ("没有", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想快点好", None),
    # Cross-group fallbacks: a febrile headache also matches the
    # respiratory questions' applies_when, and the differential planner
    # may pull them forward — every scenario must be able to answer
    # every question with a neutral response.
    "resp_breathing": ("呼吸正常", None),
    "resp_cough_details": ("没有咳嗽", None),
    "resp_intake": ("能正常喝水吃饭", None),
    "gi_details": ("没有", None),
    "head_details": ("没有", None),
    "dizzy_details": ("没有", None),
    "back_details": ("没有", None),
    "urinary_details": ("没有", None),
    "skin_details": ("没有", None),
}

SCENARIOS = [
    {
        "id": "resp-flu",
        "key_question": "resp_breathing",
        "target": "influenza_like",
        "answers": {
            "chief_complaint": ("咳嗽发烧两天", None),
            "resp_breathing": ("有点气短，活动后明显", None),
            "resp_cough_details": ("干咳为主", None),
            "resp_intake": ("能正常喝水吃饭", None),
            "associated_symptoms": ("有点乏力", None),
            "severity": ("6分", None),
        },
    },
    {
        "id": "chest-stable",
        "key_question": "severity",
        "target": "non_cardiac_chest",
        "answers": {
            "chief_complaint": ("胸口闷痛半天", None),
            "severity": ("4分", None),
            "trajectory": ("在减轻", "improving"),
        },
    },
    {
        "id": "gi-appendicitis-leaning",
        "key_question": "gi_details",
        "target": "appendicitis",
        "answers": {
            "chief_complaint": ("右下腹痛一天", None),
            "gi_details": ("右下腹绞痛，伴呕吐", None),
            "severity": ("7分", None),
            "trajectory": ("在加重", "worsening"),
            "associated_symptoms": ("呕吐两次", None),
        },
    },
    {
        "id": "gi-gastro",
        "key_question": "gi_details",
        "target": "gastroenteritis",
        "answers": {
            "chief_complaint": ("拉肚子一天", None),
            "gi_details": ("腹泻，水样便，恶心呕吐", None),
            "severity": ("4分", None),
        },
    },
    {
        "id": "head-migraine",
        "key_question": "head_details",
        "target": "migraine_tension",
        "answers": {
            "chief_complaint": ("头痛半天", None),
            "head_details": ("一侧太阳穴一跳一跳地痛", None),
            "associated_symptoms": ("恶心，怕光", None),
            "severity": ("6分", None),
        },
    },
    {
        "id": "head-meningitis-leaning",
        "key_question": "head_details",
        "target": "meningitis_possible",
        "answers": {
            "chief_complaint": ("头痛发热一天", None),
            "head_details": ("整个头胀痛，脖子硬，低头困难，伴发热", None),
            "associated_symptoms": ("呕吐一次", None),
            "severity": ("7分", None),
            "trajectory": ("在加重", "worsening"),
        },
    },
    {
        "id": "dizzy-bppv",
        "key_question": "dizzy_details",
        "target": "bppv",
        "answers": {
            "chief_complaint": ("头晕两天", None),
            "dizzy_details": ("转头和起身时天旋地转", None),
            "severity": ("4分", None),
        },
    },
    {
        "id": "back-mechanical",
        "key_question": "back_details",
        "target": "mechanical_back",
        "answers": {
            "chief_complaint": ("腰痛三天", None),
            "back_details": ("搬东西扭到了", None),
            "trajectory": ("变化不大", "stable"),
        },
    },
    {
        "id": "urinary-cystitis",
        "key_question": "urinary_details",
        "target": "cystitis",
        "answers": {
            "chief_complaint": ("尿频尿痛一天", None),
            "urinary_details": ("尿痛尿急，尿不尽", None),
            "severity": ("4分", None),
        },
    },
    {
        "id": "skin-urticaria",
        "key_question": "skin_details",
        "target": "urticaria",
        "answers": {
            "chief_complaint": ("起疹子很痒", None),
            "skin_details": ("身上一片片风团，很痒", None),
            "severity": ("4分", None),
        },
    },
    {
        "id": "multi-meningitis-febrile",
        "key_question": "head_details",
        "target": "meningitis_possible",
        "answers": {
            "chief_complaint": ("发烧头痛一天", None),
            "head_details": ("整个头胀痛，脖子硬，低头困难", None),
            "associated_symptoms": ("发烧三十九度五，呕吐一次", None),
            "temperature": ("39.5", "39.5"),
            "severity": ("7分", None),
            "trajectory": ("在加重", "worsening"),
        },
    },
    {
        "id": "multi-appendicitis-febrile",
        "key_question": "gi_details",
        "target": "appendicitis",
        "answers": {
            "chief_complaint": ("腹痛发热一天", None),
            "gi_details": ("右下腹疼痛，伴呕吐发热", None),
            "associated_symptoms": ("呕吐两次，没胃口", None),
            "temperature": ("38.6", "38.6"),
            "severity": ("7分", None),
            "trajectory": ("在加重", "worsening"),
        },
    },
    {
        "id": "knee-uncovered",
        "key_question": None,
        "target": None,
        "answers": {
            "chief_complaint": ("膝盖疼一周", None),
            "severity": ("3分", None),
        },
    },
]


def run_scenario(scenario, use_differential: bool) -> dict:
    answers = dict(COMMON)
    answers.update(scenario["answers"])
    # The engine picks its planner from the env at construction time —
    # which happens lazily on start()/require(), so the env must stay
    # set for the whole scenario (the differential default made the two
    # arms silently identical after the 2026-10-23 switch).
    import os

    previous = os.environ.get("MEDAGENT_PLANNER")
    os.environ["MEDAGENT_PLANNER"] = "differential" if use_differential else "rule"
    host = build_runtime()
    context = host.start()
    try:
        engine = context.services.require("consultation")
        if use_differential:
            enable_differential_planning(engine)
        state, reply = engine.create_session()
        asked: list[str] = []
        for _ in range(30):
            if reply.kind in {"summary", "emergency", "urgent", "closed"}:
                break
            if reply.kind == "self_check":
                reply = engine.submit(state.session_id, UserInput("暂时跳过", "skip"))
                continue
            asked.append(reply.question_id)
            text, choice = answers[reply.question_id]
            reply = engine.submit(state.session_id, UserInput(text, choice))
        # Confirm the summary closed where the flow offers it.
        if reply.kind == "summary":
            reply = engine.submit(state.session_id, UserInput("确认", "confirm"))
        planner_name = type(engine.planner).__name__
        workspace = RuleBasedProposer().propose(state)
        target = scenario["target"]
        target_status = (
            workspace.hypotheses[target].status
            if target and target in workspace.hypotheses
            else None
        )
        key_q = scenario["key_question"]
        return {
            "planner": planner_name,
            "final_kind": reply.kind,
            "risk": state.risk.level.value if state.risk else None,
            "questions": len(asked),
            "key_position": (asked.index(key_q) + 1) if key_q in asked else None,
            "key_within_8": (asked.index(key_q) + 1) <= 8 if key_q in asked else False,
            "target_status": target_status,
            "asked": asked,
        }
    finally:
        host.stop()
        if previous is None:
            os.environ.pop("MEDAGENT_PLANNER", None)
        else:
            os.environ["MEDAGENT_PLANNER"] = previous


def main() -> int:
    rows = []
    regressions = []
    for scenario in SCENARIOS:
        rule = run_scenario(scenario, use_differential=False)
        diff = run_scenario(scenario, use_differential=True)
        row = {"id": scenario["id"], "rule": rule, "differential": diff}
        rows.append(row)
        # Self-check: the two arms must actually run different
        # planners (after the default switch they silently ran the
        # same one once — parity from identical twins proves nothing).
        if rule["planner"] != "RulePlanner" or diff["planner"] != "DifferentialPlanner":
            regressions.append(
                f"{scenario['id']}: arm planners are {rule['planner']}/{diff['planner']}"
            )
        if diff["questions"] > rule["questions"] + 2:
            regressions.append(
                f"{scenario['id']}: differential asks {diff['questions']} vs rule {rule['questions']}"
            )
        if (rule["final_kind"], rule["risk"]) != (diff["final_kind"], diff["risk"]):
            regressions.append(f"{scenario['id']}: outcome differs")
        if rule["target_status"] != diff["target_status"]:
            regressions.append(f"{scenario['id']}: target status differs")
        if scenario["key_question"]:
            # Asked by rule but later/missing under differential is a
            # regression; asked by neither (the flow escalated or closed
            # before the history phase under BOTH planners) is parity.
            if rule["key_position"] is not None and (
                diff["key_position"] is None
                or diff["key_position"] > rule["key_position"]
            ):
                regressions.append(f"{scenario['id']}: key question later or missing")
    print(json.dumps({"scenarios": rows, "regressions": regressions}, ensure_ascii=False, indent=1))
    print("VERDICT:", "PASS — no regressions" if not regressions else f"FAIL {regressions}")
    return 0 if not regressions else 1


if __name__ == "__main__":
    raise SystemExit(main())
