"""Phase 1 (observe only): measure every model call site's input.

Outputs a JSON report (stdout + optional --out path) with, per call
site, character counts and tiktoken cl100k *proxy* token counts.
Deployment token counts stay null: only a live provider response can
fill them. All fixtures are synthetic — no real patient text or image.

Usage: PYTHONPATH=src python scripts/audit/phase1_measure.py [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")

from medagent.agent import AdaptiveConsultationEngine
from medagent.audit_recording import TokenCounter, measure_messages
from medagent.knowledge import ContentRepository
from medagent.triage import TriageService
from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.events import EventStore
from medagent.medical_images import SYSTEM_PROMPT as VISION_PROMPT
from medagent.subagents import KIND_INSTRUCTIONS, SubagentCoordinator
from medagent.background_review import REVIEW_INSTRUCTIONS
from medagent.reasoning import Hypothesis, ReasoningWorkspace
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService


class _ScriptBackend:
    name = "script"

    def decide(self, session_id, messages):
        return {"tool": "ask_patient", "arguments": {"fact_key": "sex"}}


class _CoordinatorStub:
    """Advertises spawn: mode_for returns advisory for model kinds."""

    mode = "advisory"

    def mode_for(self, kind):
        return "advisory" if kind in ("evidence_lookup", "second_opinion") else "off"


def _engine(coordinator=None):
    return AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=_ScriptBackend(),
        subagent_coordinator=coordinator,
    )


def _fact(key, value, evidence):
    return Fact(
        key=key,
        value=value,
        assertion=Assertion.PRESENT,
        source=Source.PATIENT,
        turn_id="t1",
        evidence=evidence,
    )


# Synthetic, plausible fact sets per pathway (invented text, not patients).
PATHWAY_FACTS = {
    "respiratory": [
        ("consult_for", "self", "我自己"),
        ("sex", "female", "女"),
        ("chief_complaint", "咳嗽发热", "咳嗽三天，发热 38.5 度"),
        ("resp_cough_details", "干咳夜间加重", "干咳，夜间加重"),
        ("temperature_c", 38.5, "38.5 度"),
        ("onset", "3 天前", "三天前开始"),
        ("trajectory", "加重", "越来越重"),
        ("severity", 6, "6 分"),
    ],
    "abdominal": [
        ("consult_for", "self", "我自己"),
        ("chief_complaint", "腹痛", "右下腹痛一天"),
        ("gi_details", "右下腹持续痛", "右下腹持续痛，不敢按压"),
        ("temperature_c", 37.8, "37.8 度"),
        ("onset", "1 天前", "昨天开始"),
        ("associated_symptoms", "恶心", "有点恶心"),
    ],
    "headache": [
        ("consult_for", "self", "我自己"),
        ("chief_complaint", "头痛", "头痛两天，怕光"),
        ("head_details", "胀痛怕光", "胀痛，见光加重"),
        ("onset", "2 天前", "两天前开始"),
    ],
}


def _state_with(engine, facts, transcript_turns=0, report_items=0):
    state = CaseState(session_id="audit-synth")
    state.active_question = "chief_complaint"
    for key, value, evidence in facts:
        state.facts[key] = _fact(key, value, evidence)
    state.transcript = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"合成对话第{i}轮"}
        for i in range(transcript_turns)
    ]
    state.report_items = [
        {
            "name": "血红蛋白",
            "reported_value": "120",
            "unit": "g/L",
            "reference_range": "110-160",
            "record_title": "血常规",
            "measured_at": "2026-10-20",
            "source": "patient_confirmed",
        }
        for _ in range(report_items)
    ]
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    counter = TokenCounter()
    report: dict = {"token_method": counter.method, "call_sites": {}}

    # --- A: adaptive decision prefix, both spawn variants ---
    engine_plain = _engine()
    prefix_plain = engine_plain._current_prefix()[0]
    engine_adv = _engine(_CoordinatorStub())
    prefix_adv = engine_adv._current_prefix()[0]
    prompt_text = engine_plain.prompt
    report["call_sites"]["A_prefix"] = {
        "prompt_chars": len(prompt_text),
        "prompt_proxy_tokens": counter.count(prompt_text)["tokens"],
        "spawn_not_advertised": measure_messages(
            [{"role": "system", "content": prefix_plain}], counter
        ),
        "spawn_advertised": measure_messages(
            [{"role": "system", "content": prefix_adv}], counter
        ),
        "catalog_json_chars": len(prefix_plain) - len(prompt_text),
    }

    # --- A: context growth curve (synthetic states) ---
    curve = []
    fact_pool = PATHWAY_FACTS["respiratory"] + PATHWAY_FACTS["abdominal"]
    for n_facts, n_transcript, n_items in [
        (0, 0, 0),
        (3, 2, 0),
        (8, 8, 5),
        (14, 16, 20),
        (20, 16, 20),
    ]:
        state = _state_with(
            engine_plain, fact_pool[:n_facts], n_transcript, n_items
        )
        messages = engine_plain._messages(state, "合成用户输入")
        measured = measure_messages(messages, counter)
        curve.append(
            {
                "facts": n_facts,
                "transcript_turns": n_transcript,
                "report_items": n_items,
                "user_chars": measured["per_message"][1]["chars"],
                "user_proxy_tokens": measured["per_message"][1]["tokens"],
                "total_chars": measured["total_chars"],
                "total_proxy_tokens": measured["total_proxy_tokens"],
            }
        )
    report["call_sites"]["A_context_curve"] = curve

    # --- A: per-pathway full messages (synthetic) ---
    pathways = {}
    for name, facts in PATHWAY_FACTS.items():
        state = _state_with(engine_plain, facts, 6, 3)
        pathways[name] = measure_messages(
            engine_plain._messages(state, "合成用户输入"), counter
        )
    report["call_sites"]["A_pathways"] = pathways

    # --- B: subagent kinds: system prompt + a real _build_view ---
    coord = SubagentCoordinator(EventStore(), _ScriptBackend(), mode="shadow")
    state = _state_with(engine_plain, PATHWAY_FACTS["respiratory"], 6, 0)
    workspace = ReasoningWorkspace()
    hyp = Hypothesis(
        id="uri", label="上呼吸道感染", category="respiratory",
        prior_band="common",
    )
    workspace.hypotheses["uri"] = hyp
    kinds = {}
    for kind in ("evidence_lookup", "second_opinion", "case_digest", "followup_batch"):
        view = coord._build_view(
            state, kind, {"objective": "合成审计目标"}, "audit-case",
            list(state.facts.keys()), ["uri"], workspace,
            "recent_window", False, None,
        )
        kinds[kind] = {
            "system_prompt": counter.count(KIND_INSTRUCTIONS[kind]),
            "view_json": counter.count(
                json.dumps(view, ensure_ascii=False, sort_keys=True)
            ),
            "first_call_messages": measure_messages(
                [
                    {"role": "system", "content": KIND_INSTRUCTIONS[kind]},
                    {"role": "user", "content": json.dumps(view, ensure_ascii=False)},
                ],
                counter,
            ),
        }
    report["call_sites"]["B_subagents"] = kinds

    # --- C: background review system prompt (snapshot size: synthetic) ---
    snapshot = {
        "facts": {k: v.to_dict() for k, v in state.facts.items()},
        "report_items": state.report_items,
    }
    report["call_sites"]["C_background_review"] = {
        "system_prompt": counter.count(REVIEW_INSTRUCTIONS),
        "synthetic_snapshot": counter.count(
            json.dumps(snapshot, ensure_ascii=False)
        ),
    }

    # --- D: vision: text prompt only; image tokens cannot be measured offline ---
    report["call_sites"]["D_vision"] = {
        "system_prompt": counter.count(VISION_PROMPT),
        "user_text": counter.count("请按固定 JSON 合同识别这张患者提供的医疗材料。"),
        "image_tokens": None,
        "image_tokens_note": "待实测：需真实视觉 tokenizer 或线上 usage；离线无法度量原图 detail=original 的图片 token",
    }

    # --- E/F: transport envelopes (no prompt content of their own) ---
    report["call_sites"]["E_compatible_transport"] = {
        "note": "传输层不自带提示词；输入即 A/B 消息原样；max_tokens 默认 2048；有界重试 1 次",
        "retry": "bounded_1",
    }
    report["call_sites"]["F_dsh_transport"] = {
        "note": "信封级黑盒：stdin 含 messages + generation 参数；内部提示词不在仓库内，无法段落级度量",
        "timeout_s": 55,
    }

    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
