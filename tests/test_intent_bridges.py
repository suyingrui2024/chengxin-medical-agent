"""Tier 1 group intent bridges (hypothesis-driven P1 package, pure functions).

Covers, in design-document order:
- §3.2 verbatim templates (GROUP_BRIDGES) and build_group_bridge / unknown group.
- question_group classification, including the P0 deep-dive definition
  (base attributes + chief-matching detail questions), the chief-only
  applies rule (applies_also_when does NOT promote), negation-aware chief
  matching, superseded-evidence corpus widening, and the P3 warning seam.
- lint_group_bridge: dynamic hypothesis/department loading, the Tier 1
  wording ban, and the 「不是」 word-boundary rule (「是不是」 survives).
- P-23 verb-dedup audit pins on the single-question anchors.

Blind-check record (mechanical, re-runnable -- not worker self-report):
~/workspace/scratch/intent-bridge-blindcheck.md lists 20 bridge rows
(4 groups x 5 chief-complaint contexts) with the per-row leak scan
(disease names / department names / predictive wording) and the blinded
conclusion for each row. The test below re-derives the same scan, so the
record can be re-verified by the supervisor without trusting this file.
"""

import sys
import types
import unittest

from medagent.bridging import (
    GROUP_BRIDGES,
    build_bridging_sentence,
    build_group_bridge,
    lint_group_bridge,
    question_group,
)
from medagent.domain import Assertion, CaseState, Fact, Source


def fact(key, value, assertion=Assertion.PRESENT, evidence=None):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t0",
        evidence=evidence,
    )


def state(chief=None, extra=None, history=None):
    facts = {}
    if chief is not None:
        facts["chief_complaint"] = fact("chief_complaint", chief)
    if extra:
        facts.update(extra)
    return CaseState(
        session_id="s-p1",
        facts=facts,
        history=history or [],
    )


# Design §3.2 verbatim templates -- pinned character for character.
EXPECTED_TEMPLATES = {
    "deep_dive": "先把你这个症状本身问清楚，部位、性质、时间这些细节会决定接下来问什么。",
    "discriminating": "接下来几个问题，是想把几种可能区分开。",
    "proactive": "除了你说的这些，我再问几个你可能没留意的表现，有没有都请直接说。",
    "background": "最后补几个背景情况，问完我就把结论整理给你。",
}


class GroupBridgeTemplateTests(unittest.TestCase):
    def test_templates_are_verbatim_design_section_3_2(self):
        self.assertEqual(GROUP_BRIDGES, EXPECTED_TEMPLATES)

    def test_templates_all_pass_group_lint(self):
        for group, template in GROUP_BRIDGES.items():
            self.assertTrue(lint_group_bridge(template), group)

    def test_build_group_bridge_returns_template(self):
        for group, template in EXPECTED_TEMPLATES.items():
            self.assertEqual(build_group_bridge(group), template)

    def test_build_group_bridge_unknown_group_returns_none(self):
        for group in ("other", "tier2", "", "deep-dive", "BACKGROUND"):
            self.assertIsNone(build_group_bridge(group))

    def test_templates_are_pairwise_distinct(self):
        values = list(GROUP_BRIDGES.values())
        self.assertEqual(len(values), len(set(values)))


class QuestionGroupTests(unittest.TestCase):
    def test_background_phase_questions(self):
        for qid in (
            "medications_taken",
            "allergies",
            "relevant_history",
            "patient_goal",
        ):
            self.assertEqual(question_group(qid, state()), "background")

    def test_associated_symptoms_is_proactive(self):
        self.assertEqual(
            question_group("associated_symptoms", state("肚子痛")), "proactive"
        )

    def test_deep_dive_base_attributes_without_chief(self):
        for qid in ("onset", "trajectory", "severity"):
            self.assertEqual(question_group(qid, state()), "deep_dive")

    def test_deep_dive_detail_matches_chief(self):
        cases = [
            ("肚子痛", "gi_details"),
            ("头痛", "head_details"),
            ("头晕", "dizzy_details"),
            ("腰痛", "back_details"),
            ("尿频", "urinary_details"),
            ("咳嗽", "resp_cough_details"),
            ("咳嗽", "resp_breathing"),
            ("咳嗽", "resp_intake"),
            ("发烧", "reported_temperature"),
            ("眼睛红", "eye_warning_signs"),
        ]
        for chief, qid in cases:
            with self.subTest(chief=chief, qid=qid):
                self.assertEqual(question_group(qid, state(chief)), "deep_dive")

    def test_non_matching_detail_is_discriminating(self):
        self.assertEqual(
            question_group("head_details", state("肚子痛")), "discriminating"
        )
        self.assertEqual(
            question_group("gi_details", state("头痛")), "discriminating"
        )
        self.assertEqual(question_group("gi_details", state()), "discriminating")

    def test_denied_chief_term_does_not_match(self):
        # Immediate 「不是<term>」 names the term only to correct it away.
        denied = state("不是肚子不舒服，是胃胀")
        self.assertEqual(question_group("gi_details", denied), "discriminating")
        # Negation cue: 「没有肚子痛」 must not promote gi_details, while
        # the affirmed 「头晕」 still promotes dizzy_details.
        negated = state("没有肚子痛，只是头晕")
        self.assertEqual(question_group("gi_details", negated), "discriminating")
        self.assertEqual(question_group("dizzy_details", negated), "deep_dive")

    def test_superseded_chief_evidence_widens_corpus(self):
        superseded = fact(
            "chief_complaint", "胃胀", evidence="肚子不舒服"
        )
        st = state("胃胀", history=[superseded])
        self.assertEqual(question_group("gi_details", st), "deep_dive")

    def test_wiped_case_inherits_no_previous_chief(self):
        superseded = fact(
            "chief_complaint", "胃胀", evidence="肚子不舒服"
        )
        st = CaseState(session_id="s-p1", facts={}, history=[superseded])
        self.assertEqual(question_group("gi_details", st), "discriminating")

    def test_applies_also_when_does_not_promote_to_deep_dive(self):
        # Fever surfacing only via associated_symptoms (the
        # reported_temperature applies_also_when path) is NOT a
        # chief-complaint applies_when match, so the question stays
        # discriminating. This pins the chief-only reading of the P0
        # deep-dive definition; P2/P0 seam owners must not silently
        # widen it without updating this test.
        st = state(
            "咳嗽",
            extra={
                "associated_symptoms": fact(
                    "associated_symptoms", "发烧38度"
                )
            },
        )
        self.assertEqual(
            question_group("reported_temperature", st), "discriminating"
        )

    def test_other_phases_and_unknown_ids(self):
        for qid in ("consult_for", "sex", "age", "chief_complaint"):
            self.assertEqual(question_group(qid, state("肚子痛")), "other")
        self.assertEqual(
            question_group("emergency_screen", state("肚子痛")), "other"
        )
        self.assertEqual(
            question_group("no_such_question", state("肚子痛")), "other"
        )

    def test_p3_warning_seam_proactive_when_module_present(self):
        stub = types.ModuleType("medagent.out_of_pathway")
        stub.warning_questions = lambda chief: [
            {"id": "warning_consciousness"},
            {"fact_key": "warning_breathing"},
            "warning_bleeding",
        ]
        sys.modules["medagent.out_of_pathway"] = stub
        try:
            for qid in (
                "warning_consciousness",
                "warning_breathing",
                "warning_bleeding",
            ):
                self.assertEqual(
                    question_group(qid, state("眼睛红")), "proactive"
                )
        finally:
            sys.modules.pop("medagent.out_of_pathway", None)

    def test_p3_warning_seam_degrades_when_module_absent(self):
        # sys.modules[...] = None makes the import raise ImportError,
        # simulating a tree where P3 has not landed yet.
        previous = sys.modules.get("medagent.out_of_pathway", None)
        sys.modules["medagent.out_of_pathway"] = None
        try:
            self.assertEqual(
                question_group("warning_consciousness", state("眼睛红")),
                "other",
            )
        finally:
            if previous is None:
                sys.modules.pop("medagent.out_of_pathway", None)
            else:
                sys.modules["medagent.out_of_pathway"] = previous

    def test_p3_warning_seam_real_module_domain_ids(self):
        # Seam against the real P3 module (not a stub): off-pathway
        # chiefs yield warning_<domain> ids that classify proactive,
        # while an on-pathway chief yields none of them.
        try:
            from medagent.out_of_pathway import warning_questions
        except ImportError:
            self.skipTest("P3 module not present in this tree")
        off_pathway = state("肥胖")
        derived = [
            f"warning_{item['domain']}"
            for item in warning_questions("肥胖")
        ]
        self.assertGreater(len(derived), 0)
        for qid in derived:
            with self.subTest(qid=qid):
                self.assertEqual(question_group(qid, off_pathway), "proactive")
        on_pathway = state("肚子痛")
        for qid in derived:
            with self.subTest(qid=qid):
                self.assertEqual(question_group(qid, on_pathway), "other")

    def test_p3_warning_seam_degrades_when_p3_raises(self):
        stub = types.ModuleType("medagent.out_of_pathway")

        def boom(chief):
            raise RuntimeError("p3 not ready")

        stub.warning_questions = boom
        sys.modules["medagent.out_of_pathway"] = stub
        try:
            self.assertEqual(
                question_group("warning_consciousness", state("眼睛红")),
                "other",
            )
        finally:
            sys.modules.pop("medagent.out_of_pathway", None)


class LintGroupBridgeTests(unittest.TestCase):
    def test_all_hypothesis_labels_are_intercepted(self):
        import json
        from importlib import resources

        raw = json.loads(
            resources.files("medagent.content")
            .joinpath("differentials.json")
            .read_text(encoding="utf-8")
        )
        labels = set()
        for pathway in raw["pathways"]:
            for hypothesis in pathway["hypotheses"]:
                labels.add(hypothesis["label"])
                labels.add(hypothesis["patient_label"])
        self.assertGreater(len(labels), 20)
        for label in labels:
            with self.subTest(label=label):
                self.assertFalse(
                    lint_group_bridge(f"接下来问问{label}的情况。")
                )

    def test_all_department_names_are_intercepted(self):
        import json
        from importlib import resources

        raw = json.loads(
            resources.files("medagent.content")
            .joinpath("departments.json")
            .read_text(encoding="utf-8")
        )
        names = set()

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("department"), str):
                    names.add(node["department"])
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(raw)
        self.assertGreater(len(names), 5)
        for name in names:
            with self.subTest(name=name):
                self.assertFalse(
                    lint_group_bridge(f"这个问题可以去{name}看看。")
                )

    def test_predictive_and_conclusive_wording_intercepted(self):
        for word in (
            "初步考虑",
            "很像",
            "倾向于",
            "排除",
            "不是",
            "急诊",
            "住院",
            "留观",
            "手术",
            "确诊",
            "可以排除",
            "毫克",
            "服用",
        ):
            with self.subTest(word=word):
                self.assertFalse(
                    lint_group_bridge(f"接下来问问，{word}的情况。")
                )

    def test_bare_bushi_variants_intercepted(self):
        for text in (
            "这不是感冒的情况。",
            "不是肚子痛的问题。",
            "先说不是，再问别的。",
        ):
            with self.subTest(text=text):
                self.assertFalse(lint_group_bridge(text))

    def test_shi_bushi_retelling_is_not_killed(self):
        # 「是不是」 is a question echo / patient-utterance retelling,
        # not a conclusion -- the 「不是」 ban must not fire on it.
        for text in (
            "你是不是还有别的表现？",
            "你说是不是发热，我再确认一下。",
            "你是不是说没有发热？",
        ):
            with self.subTest(text=text):
                self.assertTrue(lint_group_bridge(text))

    def test_mixed_shi_bushi_and_bare_bushi_still_intercepted(self):
        # A clean 「是不是」 echo does not launder a separate bare
        # 「不是」 conclusion in the same sentence.
        self.assertFalse(
            lint_group_bridge("你是不是说没有发热？不是感冒。")
        )

    def test_clean_bridge_text_passes(self):
        self.assertTrue(lint_group_bridge("你说肚子痛，再问一句。"))
        self.assertTrue(lint_group_bridge("接下来几个问题，想先确认一下。"))


class BlindCheckMechanicalTests(unittest.TestCase):
    """Re-derivation of ~/workspace/scratch/intent-bridge-blindcheck.md.

    The shipped bridge text is a constant per group: it cannot encode
    per-case ordering, because the same string is emitted whatever the
    chief complaint or the engine's internal ranking. This test pins
    that context-invariance across five chief complaints, plus the
    zero-leak scan for every row.
    """

    CONTEXTS = ("肚子痛", "头痛", "咳嗽", "腰痛", "眼睛红")

    def test_bridge_text_is_context_invariant_and_leak_free(self):
        import json
        from importlib import resources

        diff = json.loads(
            resources.files("medagent.content")
            .joinpath("differentials.json")
            .read_text(encoding="utf-8")
        )
        labels = set()
        for pathway in diff["pathways"]:
            for hypothesis in pathway["hypotheses"]:
                labels.add(hypothesis["label"])
                labels.add(hypothesis["patient_label"])
        dept_raw = json.loads(
            resources.files("medagent.content")
            .joinpath("departments.json")
            .read_text(encoding="utf-8")
        )
        departments = set()

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("department"), str):
                    departments.add(node["department"])
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(dept_raw)
        rows = 0
        for group, template in GROUP_BRIDGES.items():
            for chief in self.CONTEXTS:
                rows += 1
                with self.subTest(group=group, chief=chief):
                    # Same string whatever the case: no ordering signal.
                    self.assertEqual(build_group_bridge(group), template)
                    self.assertTrue(lint_group_bridge(template))
                    self.assertFalse(
                        any(label in template for label in labels)
                    )
                    self.assertFalse(
                        any(name in template for name in departments)
                    )
        self.assertEqual(rows, 20)


class P23VerbDedupTests(unittest.TestCase):
    """P-23 audit: anchor templates must not double their own verbs.

    Each case feeds a slot that already carries the template's verb and
    asserts the rendered anchor carries it exactly once. Existing
    single-question behaviour is otherwise untouched (see the regression
    pins at the end of this class).
    """

    def _anchor(self, facts, fact_key, turn_index):
        sentence, kind = build_bridging_sentence(
            facts, fact_key, "再问一个问题？", [], turn_index
        )
        self.assertEqual(kind, "anchor")
        return sentence

    def test_onset_slot_leading_cong_is_not_doubled(self):
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "onset": fact("onset", "从昨天开始"),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "从昨天开始到现在，再问一句：")

    def test_onset_slot_trailing_daoxianzai_is_not_doubled(self):
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "onset": fact("onset", "三天前开始到现在"),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "从三天前开始到现在，再问一句：")

    def test_onset_slot_both_ends_stripped(self):
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "onset": fact("onset", "从三天前开始到现在"),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "从三天前开始到现在，再问一句：")

    def test_severity_slot_leading_dayue_is_not_doubled(self):
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "severity": fact("severity", "大约5分"),
            },
            "onset",
            1,
        )
        self.assertEqual(sentence, "你说现在大约5分，再问一句：")

    def test_goal_slot_worry_verb_is_not_doubled(self):
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "patient_goal": fact("patient_goal", "担心会越来越严重"),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "你最担心的是会越来越严重，再问一句：")

    def test_associated_slot_mention_verb_is_not_doubled(self):
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "肚子痛"),
                "associated_symptoms": fact(
                    "associated_symptoms", "提到发热、咳嗽"
                ),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "除了肚子痛，你还提到发热、咳嗽，再问一句：")

    def test_detail_slot_mention_verb_is_not_doubled(self):
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "肚子痛"),
                "gi_details": fact("gi_details", "提到右下腹痛"),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "你还提到右下腹痛，再问一句：")

    def test_exposures_slot_mention_verb_is_not_doubled(self):
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "recent_exposures": fact(
                    "recent_exposures", "提到过新换的洗衣液"
                ),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "你还提到新换的洗衣液，再问一句：")

    def test_denial_slot_denial_word_is_not_doubled(self):
        sentence, kind = build_bridging_sentence(
            {
                "associated_symptoms": fact(
                    "associated_symptoms",
                    "没有发热",
                    assertion=Assertion.ABSENT,
                )
            },
            "associated_symptoms",
            "还有别的表现吗？",
            [],
            0,
        )
        self.assertEqual(kind, "denial")
        self.assertEqual(sentence, "前面你说过没有发热，这里再确认一下相关的部分：")

    def test_pre_existing_dedup_regressions_unchanged(self):
        # Batch C pins, re-asserted here so the P-23 widening cannot
        # silently move them: trailing 开始 / trailing 了 / medication
        # leading verbs keep exactly their old rendering.
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "onset": fact("onset", "一天半前开始"),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "从一天半前开始到现在，再问一句：")
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "onset": fact("onset", "两天了"),
            },
            "severity",
            1,
        )
        self.assertEqual(sentence, "从两天开始到现在，再问一句：")
        sentence = self._anchor(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "medications_taken": fact(
                    "medications_taken", "自己吃过几天益生菌"
                ),
            },
            "severity",
            1,
        )
        # Rotation: chief(0) then medications(1) with only these two
        # candidates present... medications is the second candidate only
        # when no onset/severity/associated/history/goal/detail/exposure
        # candidate precedes it; here candidates are [chief, medications].
        self.assertIn("用过几天益生菌", sentence)
        self.assertNotIn("用过自己吃过", sentence)


if __name__ == "__main__":
    unittest.main()
