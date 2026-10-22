"""Layered triage (design phase 1): data-driven red flags, curated-pattern
recall, negation discipline, single-computation caching, provider contract,
and the corpus gate."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from medagent.domain import CaseState, RiskLevel
from medagent.red_flags import FlagHit, RedFlagScanner, load_definitions
from medagent.triage import TriageService

FIXTURES = Path(__file__).parent / "fixtures"


def load_corpus(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class DefinitionTests(unittest.TestCase):
    def test_definitions_cover_the_six_emergency_flags(self):
        ids = {d.id for d in load_definitions()}
        self.assertEqual(
            ids,
            {"unresponsive", "seizure", "cyanosis", "severe_breathing",
             "chest_pain", "major_bleeding"},
        )

    def test_phrase_layer_preserves_historical_labels(self):
        service = TriageService()
        self.assertEqual(service.scan_text("患者失去意识了"), ["意识明显异常"])
        self.assertEqual(service.scan_text("持续胸痛十几分钟"), ["高风险胸痛描述"])
        self.assertEqual(service.scan_text("大出血"), ["大量或无法控制的出血"])


class PatternLayerTests(unittest.TestCase):
    def setUp(self):
        self.service = TriageService()

    def test_paraphrases_trigger(self):
        cases = {
            "我爸怎么喊都没反应，推他也不动": "意识明显异常",
            "他全身发抖，停不下来，眼睛往上翻": "抽搐",
            "他嘴唇都紫了": "口唇发紫",
            "呼吸特别费劲，感觉吸不进气": "严重呼吸困难",
            "胸口像压了块石头一样疼，还出冷汗": "高风险胸痛描述",
            "吐了好多血，吐了两回了": "大量或无法控制的出血",
        }
        for text, label in cases.items():
            with self.subTest(text=text):
                self.assertIn(label, self.service.scan_text(text))

    def test_negations_do_not_trigger(self):
        for text in [
            "他没有失去意识，就是睡着了",
            "没有抽搐，就是有点发抖，可能是冷的",
            "嘴唇没有发紫，就是有点干",
            "没有呼吸困难，就是有点鼻塞",
            "没有胸痛，就是胃有点胀",
            "否认胸痛，心电图刚做完没事",
            "胸口不疼了，昨天就是岔气",
            "血已经止住了，没有再流了",
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.service.scan_text(text), [])

    def test_measurement_compounds_do_not_false_trigger(self):
        self.assertEqual(self.service.scan_text("血压有点高，120/85，胸围92"), [])

    def test_scan_detailed_reports_layers(self):
        hits = self.service.scan_detailed("他嘴唇都紫了")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].flag_id, "cyanosis")
        self.assertEqual(hits[0].layer, "semantic_pattern")
        hits = self.service.scan_detailed("患者失去意识了")
        self.assertEqual(hits[0].layer, "rule_phrase")


class CacheTests(unittest.TestCase):
    def test_scan_is_memoized_per_text(self):
        scanner = RedFlagScanner()
        calls = []
        original = scanner.scan

        def counting(text):
            calls.append(text)
            return original(text)

        scanner.scan = counting
        service = TriageService(scanner=scanner)
        text = "患者失去意识了"
        self.assertEqual(service.scan_text(text), service.scan_text(text))
        self.assertEqual(calls, [text])


class _StubProvider:
    def __init__(self, hits):
        self._hits = hits

    def scan(self, text):
        return list(self._hits)


class ProviderContractTests(unittest.TestCase):
    def test_provider_can_only_add_known_flags_with_verbatim_evidence(self):
        provider = _StubProvider([
            FlagHit("seizure", "抽搐", "provider", "全身抽动"),
            FlagHit("unknown_flag", "?", "provider", "全身抽动"),
            FlagHit("cyanosis", "口唇发紫", "provider", "并不存在的证据"),
        ])
        service = TriageService(semantic_provider=provider)
        labels = service.scan_text("宝宝全身抽动")
        self.assertIn("抽搐", labels)
        self.assertNotIn("口唇发紫", labels)

    def test_provider_cannot_remove_rule_hits(self):
        service = TriageService(semantic_provider=_StubProvider([]))
        self.assertEqual(service.scan_text("患者昏迷"), ["意识明显异常"])


class AssessCompatibilityTests(unittest.TestCase):
    def test_assess_still_escalates_on_paraphrase(self):
        service = TriageService()
        state = CaseState(session_id="t")
        result = service.assess(state, "胸口像压了块石头一样疼，还出冷汗")
        self.assertEqual(result.level, RiskLevel.EMERGENCY)

    def test_assess_blank_state_undetermined_without_input(self):
        service = TriageService()
        state = CaseState(session_id="t")
        self.assertEqual(service.assess(state, "最近有点失眠").level, RiskLevel.UNDETERMINED)


class CorpusGateTests(unittest.TestCase):
    def _run(self, name: str):
        corpus = load_corpus(name)
        service = TriageService()
        rows = []
        for sample in corpus["samples"]:
            got = bool(service.scan_text(sample["text"]))
            rows.append((sample, got == sample["expect_trigger"]))
        return rows

    def test_corpus_v0_fully_passes(self):
        rows = self._run("red_flag_corpus_v0.json")
        failures = [s["id"] for s, ok in rows if not ok]
        self.assertEqual(failures, [])

    def test_probe_v1_recall_and_fpr(self):
        rows = self._run("red_flag_probe_v1.json")
        failures = [s["id"] for s, ok in rows if not ok]
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
