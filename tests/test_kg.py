"""Knowledge base tests: provenance, normalisation, and honest limits."""

import os
import tempfile
import unittest
from pathlib import Path

from medagent.extensions.builtin import load_registry
from medagent.kg import Concept, ConceptStore, Normalizer, Relation, seed
from medagent.kg.loaders import load_terms, load_tsv


class ConceptTests(unittest.TestCase):
    def test_kind_must_be_known(self):
        with self.assertRaises(ValueError):
            Concept(id="x", kind="vibes", canonical="x", source="s", review_status="r")

    def test_provenance_is_mandatory(self):
        for kwargs in (
            {"id": "x", "kind": "symptom", "canonical": "x", "source": "", "review_status": "r"},
            {"id": "x", "kind": "symptom", "canonical": "x", "source": "s", "review_status": ""},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    Concept(**kwargs)

    def test_relation_requires_a_source(self):
        with self.assertRaises(ValueError):
            Relation(src="a", predicate="p", dst="b", source="")


class StoreTests(unittest.TestCase):
    def build(self):
        store = ConceptStore.in_memory()
        store.add(
            [
                Concept(
                    id="symptom:chest_pain",
                    kind="symptom",
                    canonical="胸痛",
                    source="test",
                    review_status="test",
                    aliases=("胸痛", "胸口痛", "心口疼"),
                ),
                Concept(
                    id="symptom:abd_pain",
                    kind="symptom",
                    canonical="腹痛",
                    source="test",
                    review_status="test",
                    aliases=("腹痛", "肚子疼"),
                ),
            ]
        )
        return store

    def test_counts(self):
        store = self.build()
        self.assertEqual(store.counts(), {"concepts": 2, "aliases": 5, "relations": 0})

    def test_aliases_are_longest_first(self):
        store = self.build()
        texts = [text for text, _ in store.all_aliases()]
        self.assertEqual(texts, sorted(texts, key=len, reverse=True))

    def test_export_round_trip(self):
        store = self.build()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "kg.json"
            store.export(path)
            self.assertTrue(path.is_file())
            payload = path.read_text(encoding="utf-8")
            self.assertIn("胸口痛", payload)


class NormalizerTests(unittest.TestCase):
    def setUp(self):
        store = ConceptStore.in_memory()
        store.add(
            [
                Concept(
                    id="symptom:chest_pain",
                    kind="symptom",
                    canonical="胸痛",
                    source="t",
                    review_status="t",
                    aliases=("胸痛", "胸口痛", "心口疼"),
                ),
                Concept(
                    id="symptom:abd_pain",
                    kind="symptom",
                    canonical="腹痛",
                    source="t",
                    review_status="t",
                    aliases=("腹痛", "肚子疼"),
                ),
            ]
        )
        self.normalizer = Normalizer(store)

    def test_colloquial_wording_maps_to_the_concept(self):
        matches = self.normalizer.find("我这几天心口疼得厉害")
        self.assertEqual([m.concept_id for m in matches], ["symptom:chest_pain"])
        self.assertEqual(matches[0].text, "心口疼")

    def test_span_points_at_the_original_text(self):
        text = "肚子疼两天了"
        match = self.normalizer.find(text)[0]
        self.assertEqual(text[match.start : match.end], "肚子疼")

    def test_no_cross_contamination_between_concepts(self):
        self.assertEqual(self.normalizer.concepts("心口疼"), ["symptom:chest_pain"])
        self.assertEqual(self.normalizer.concepts("肚子疼"), ["symptom:abd_pain"])

    def test_unknown_text_yields_nothing(self):
        self.assertEqual(self.normalizer.find("今天天气不错"), [])

    def test_empty_input_is_safe(self):
        self.assertEqual(self.normalizer.find(""), [])

    def test_kind_filter(self):
        self.assertTrue(self.normalizer.has("心口疼", "symptom"))
        self.assertFalse(self.normalizer.has("心口疼", "lab"))


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()
        self.store = seed.build(self.registry)
        self.normalizer = Normalizer(self.store)

    def test_seed_covers_every_source_file(self):
        counts: dict[str, int] = {}
        for concept in self.store.concepts():
            counts[concept["kind"]] = counts.get(concept["kind"], 0) + 1
        # One assertion per source, so a silently empty file shows up as a
        # specific failure instead of a vague total.
        self.assertGreaterEqual(counts.get("scale", 0), 30)
        self.assertGreaterEqual(counts.get("formula", 0), 15)
        self.assertGreaterEqual(counts.get("red_flag", 0), 5)
        self.assertGreaterEqual(counts.get("lab", 0), 3)
        self.assertGreaterEqual(counts.get("department", 0), 5)

    def test_every_seeded_concept_carries_provenance(self):
        for concept in self.store.concepts():
            with self.subTest(concept["id"]):
                self.assertTrue(concept["source"])
                self.assertTrue(concept["review_status"])

    def test_capability_names_are_findable(self):
        # A patient naming a scale by its published alias must resolve.
        self.assertEqual(
            self.normalizer.concepts("帮我做个 PHQ-8"), ["capability:calculate.phq8"]
        )

    def test_red_flags_and_labs_are_present(self):
        kinds = {concept["kind"] for concept in self.store.concepts()}
        self.assertIn("red_flag", kinds)
        self.assertIn("lab", kinds)
        self.assertIn("department", kinds)

    def test_seed_adds_no_inferred_relations(self):
        # Relations are clinical judgement; the seed must not invent them.
        self.assertEqual(self.store.relations(), [])


class LoaderTests(unittest.TestCase):
    def test_tsv_collects_aliases_under_one_concept(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "terms.tsv"
            path.write_text("胸痛\t胸口痛\n胸痛\t心口疼\n腹痛\t肚子疼\n", encoding="utf-8")
            concepts = load_tsv(path, kind="symptom", source="test-source")
        self.assertEqual(len(concepts), 2)
        chest = next(c for c in concepts if c.canonical == "胸痛")
        self.assertEqual(set(chest.aliases), {"胸痛", "胸口痛", "心口疼"})
        self.assertEqual(chest.review_status, "imported_pending_review")

    def test_plain_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "terms.txt"
            path.write_text("胸痛\n腹痛\n\n# 注释\n", encoding="utf-8")
            concepts = load_terms(path, kind="symptom", source="test-source")
        self.assertEqual([c.canonical for c in concepts], ["胸痛", "腹痛"])

    def test_imported_concepts_are_not_marked_reviewed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "terms.txt"
            path.write_text("胸痛\n", encoding="utf-8")
            concept = load_terms(path, kind="symptom", source="s")[0]
        self.assertNotIn("reviewed", concept.review_status)


if __name__ == "__main__":
    unittest.main()


BUILT = Path(__file__).resolve().parents[1] / "data" / "kg" / "kg.db"


@unittest.skipUnless(BUILT.is_file(), "知识库尚未装配，先运行 scripts/build/build_kg.py")
class BuiltKnowledgeBaseTests(unittest.TestCase):
    """Asserts on the assembled database, not on the seed.

    These are the numbers a reviewer will ask about, so they are pinned:
    if an import silently drops half the rows, this fails instead of the
    library quietly getting smaller.
    """

    @classmethod
    def setUpClass(cls):
        cls.store = ConceptStore.open(BUILT)
        cls.normalizer = Normalizer(cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()

    def test_scale_of_the_import(self):
        counts = self.store.counts()
        self.assertGreaterEqual(counts["concepts"], 2000)
        self.assertGreaterEqual(counts["aliases"], 3000)
        self.assertGreaterEqual(counts["relations"], 600)

    def test_imported_rows_are_not_marked_reviewed(self):
        for concept in self.store.concepts():
            if concept["source"].startswith(("ICD-10-CN", "Wikidata")):
                with self.subTest(concept["id"]):
                    self.assertEqual(concept["review_status"], "imported_pending_review")

    def test_icd_symptom_chapter_is_not_filed_as_disease(self):
        # R51 头痛 is a complaint, not a diagnosis.
        headache = self.store.get("icd10:R51")
        self.assertIsNotNone(headache)
        self.assertEqual(headache["kind"], "symptom")

    def test_headache_resolves_to_a_symptom(self):
        matches = self.normalizer.find("最近老是头痛")
        self.assertEqual([m.kind for m in matches], ["symptom"])
        self.assertEqual(matches[0].canonical, "头痛")

    def test_a_symptom_reaches_its_diseases(self):
        edges = self.store.relations("symptom:wd:头痛")
        self.assertGreater(len(edges), 20)

    def test_capability_aliases_survive_the_import(self):
        self.assertEqual(
            self.normalizer.concepts("帮我做个 PHQ-8"), ["capability:calculate.phq8"]
        )

    def test_no_relation_dangles(self):
        known = {concept["id"] for concept in self.store.concepts()}
        for edge in self.store.relations():
            with self.subTest(edge["src"]):
                self.assertIn(edge["src"], known)
                self.assertIn(edge["dst"], known)


class ShadowObservationTests(unittest.TestCase):
    """The knowledge base observes a turn and never influences it.

    That contract is the whole reason it is safe to ship: if the graph
    ever changes a reply, or a broken database ever costs a turn, the
    feature is worse than not having it.
    """

    def make_engine(self):
        from medagent.agent import AdaptiveConsultationEngine
        from medagent.events import EventStore
        from medagent.knowledge import ContentRepository
        from medagent.reasoner import OrderedQuestionRanker
        from medagent.self_checks import SelfCheckService
        from medagent.triage import TriageService

        class Backend:
            name = "fixture"

            def __init__(self):
                self.commands = []

            def decide(self, identity, messages):
                return self.commands.pop(0)

        backend = Backend()
        events = EventStore()
        engine = AdaptiveConsultationEngine(
            content=ContentRepository(load_registry()),
            triage=TriageService(),
            self_checks=SelfCheckService(load_registry()),
            events=events,
            ranker=OrderedQuestionRanker(),
            backend=backend,
        )
        return engine, events, backend

    def turn(self, text):
        from medagent.consultation import UserInput

        engine, events, backend = self.make_engine()
        state, _ = engine.create_session()
        backend.commands.append(
            {
                "tool": "clarify",
                "arguments": {"message": "这种情况多久了？", "topic": "chief_complaint"},
            }
        )
        reply = engine.submit(state.session_id, UserInput(text))
        observed = [
            event
            for event in events.for_session(state.session_id)
            if event.event_type == "kg.normalized"
        ]
        return reply, observed

    @unittest.skipUnless(BUILT.is_file(), "知识库尚未装配")
    def test_recognised_text_is_recorded(self):
        reply, observed = self.turn("最近老是头痛，还有点恶心")
        self.assertEqual(reply.kind, "question")
        self.assertEqual(len(observed), 1)
        concepts = {match["canonical"] for match in observed[0].payload["matches"]}
        self.assertEqual(concepts, {"头痛", "恶心"})

    @unittest.skipUnless(BUILT.is_file(), "知识库尚未装配")
    def test_unrecognised_text_records_nothing(self):
        _, observed = self.turn("今天天气不错")
        self.assertEqual(observed, [])

    def test_observation_never_changes_the_reply(self):
        from medagent.kg import runtime

        text = "最近老是头痛，还有点恶心"
        with_kg, _ = self.turn(text)
        previous = os.environ.get("MEDAGENT_KG")
        os.environ["MEDAGENT_KG"] = "off"
        runtime.reset()
        try:
            without_kg, observed = self.turn(text)
        finally:
            if previous is None:
                os.environ.pop("MEDAGENT_KG", None)
            else:
                os.environ["MEDAGENT_KG"] = previous
            runtime.reset()
        self.assertEqual(observed, [])
        self.assertEqual(with_kg.kind, without_kg.kind)
        self.assertEqual(with_kg.message, without_kg.message)


class CorpusCoverageFloorTests(unittest.TestCase):
    """A floor under the only clinical evaluation we have.

    The red-flag corpus is small and unreviewed, but it is real text. The
    floor tracks the gains: 19% before the ICD title rules, 32% after,
    65% once the triage rules' own phrases became aliases. A future
    change that quietly loses any of those layers would drop back without
    anyone noticing.
    """

    @unittest.skipUnless(BUILT.is_file(), "知识库尚未装配")
    def test_red_flag_corpus_coverage_does_not_regress(self):
        import json

        corpus = json.loads(
            (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "red_flag_corpus_v0.json")
            .read_text(encoding="utf-8")
        )["samples"]
        normalizer = Normalizer(ConceptStore.open(BUILT))
        positives = [s for s in corpus if s["expect_trigger"]]
        seen = sum(
            1
            for sample in positives
            if any(
                match.kind in {"symptom", "disease", "red_flag"}
                for match in normalizer.find(sample["text"])
            )
        )
        coverage = seen / len(positives)
        self.assertGreaterEqual(
            coverage, 0.60, f"红旗语料覆盖率跌到 {coverage:.0%}，低于 60% 下限"
        )


class ImportedRelationTests(unittest.TestCase):
    """Relations from DiaKG: the axes ICD and Wikidata do not provide."""

    @classmethod
    def setUpClass(cls):
        cls.store = ConceptStore.open(BUILT)
        cls.predicates = {edge["predicate"] for edge in cls.store.relations()}

    @classmethod
    def tearDownClass(cls):
        cls.store.close()

    @unittest.skipUnless(BUILT.is_file(), "知识库尚未装配")
    def test_drug_relations_are_present(self):
        # Nothing else in the graph knows about drugs at all.
        self.assertIn("treats", self.predicates)
        self.assertIn("causes_adverse_event", self.predicates)
        self.assertIn("has_test", self.predicates)

    @unittest.skipUnless(BUILT.is_file(), "知识库尚未装配")
    def test_entities_resolve_across_sources(self):
        # A DiaKG drug pointing at a Wikidata symptom means the resolver
        # matched by canonical name instead of minting a duplicate node.
        edges = [
            edge
            for edge in self.store.relations()
            if edge["predicate"] == "causes_adverse_event"
        ]
        self.assertTrue(edges)
        cross = [
            edge
            for edge in edges
            if edge["src"].startswith("diakg:") and not edge["dst"].startswith("diakg:")
        ]
        self.assertTrue(cross, "DiaKG 实体没有复用已有概念，图谱被切碎了")

    @unittest.skipUnless(BUILT.is_file(), "知识库尚未装配")
    def test_every_relation_endpoint_exists(self):
        known = {concept["id"] for concept in self.store.concepts()}
        for edge in self.store.relations():
            with self.subTest(edge["src"]):
                self.assertIn(edge["src"], known)
                self.assertIn(edge["dst"], known)
