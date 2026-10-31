"""Catalog drift guard: code, content and the human-readable table agree.

Three artefacts describe the same 154 items: ``extensions/catalog.py``
(machine), ``packs/*.json`` (content) and
``docs/门诊显式计算科目与项目总表.md`` (human). This test is what stops
them from drifting — the previous hand-written whitelist had already
listed 29 ids for 4 packs before anyone noticed.
"""

import json
import re
import unittest
from importlib import resources
from pathlib import Path

from medagent.extensions.builtin import load_registry
from medagent.extensions.catalog import (
    CATALOG,
    DELIVERED_IDS,
    STATUSES,
    by_department,
    summary,
)
from medagent.extensions.formula_forms import formula_form_spec
from medagent.extensions.scale_forms import scale_form_spec

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "门诊显式计算科目与项目总表.md"


def pack_files():
    root = resources.files("medagent.extensions").joinpath("packs")
    return {
        json.loads(file.read_text(encoding="utf-8"))["id"]
        for file in root.iterdir()
        if file.name.endswith(".json")
    }


class CatalogIntegrityTests(unittest.TestCase):
    def test_no_duplicate_codes_or_ids(self):
        codes = [entry.code for entry in CATALOG]
        self.assertEqual(len(codes), len(set(codes)))
        ids = [entry.id for entry in CATALOG if entry.id]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_status_is_registered(self):
        for entry in CATALOG:
            self.assertIn(entry.status, STATUSES, entry.code)

    def test_delivered_rows_have_ids_and_others_may_not(self):
        for entry in CATALOG:
            if entry.status == "delivered":
                self.assertIsNotNone(entry.id, entry.code)

    def test_counts_are_consistent(self):
        counts = summary()
        self.assertEqual(counts["total"], len(CATALOG))
        self.assertEqual(counts["patient"] + counts["clinician"], counts["total"])
        self.assertEqual(
            sum(counts[status] for status in STATUSES), counts["total"]
        )

    def test_departments_are_grouped(self):
        grouped = by_department()
        self.assertGreaterEqual(len(grouped), 25)
        self.assertEqual(sum(len(items) for items in grouped.values()), len(CATALOG))


class ContentMatchesCatalogTests(unittest.TestCase):
    # The four first-party capabilities predate the department catalog and
    # are registered directly in ``builtin.IMPLEMENTATIONS``.
    BASE_PACK_IDS = {
        "calculate.units",
        "calculate.glucose",
        "observe.temperature",
        "observe.location",
    }

    def test_pack_files_are_exactly_the_delivered_ids(self):
        self.assertEqual(pack_files() - self.BASE_PACK_IDS, set(DELIVERED_IDS))

    def test_planned_and_pending_ids_have_no_pack(self):
        # A pack that exists before its row flips to delivered would be
        # content going live without the catalog change that allows it.
        for entry in CATALOG:
            if entry.status in {"planned", "clinician_pending"} and entry.id:
                self.assertNotIn(entry.id, pack_files(), entry.code)

    def test_every_pack_validates_against_its_engine(self):
        root = resources.files("medagent.extensions").joinpath("packs")
        for file in root.iterdir():
            if not file.name.endswith(".json"):
                continue
            manifest = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(manifest.get("scale"), dict):
                scale_form_spec(manifest)
            elif isinstance(manifest.get("formula"), dict):
                formula_form_spec(manifest)

    def test_registry_loads_only_delivered(self):
        registry = load_registry()
        loaded = {item["id"] for item in registry.descriptions()}
        # Observe capabilities have no execute() and never appear here.
        self.assertTrue(DELIVERED_IDS <= loaded | {"calculate.units", "calculate.glucose"})
        self.assertEqual(loaded - {"calculate.units", "calculate.glucose"}, set(DELIVERED_IDS))


class DocumentMatchesCatalogTests(unittest.TestCase):
    def test_document_exists(self):
        self.assertTrue(DOC.is_file(), f"缺少目录文档：{DOC}")

    def test_document_lists_every_code(self):
        text = DOC.read_text(encoding="utf-8")
        listed = set(re.findall(r"\|\s*([A-Z]{2,3}-\d{2})\s*\|", text))
        expected = {entry.code for entry in CATALOG}
        self.assertEqual(listed - expected, set(), "文档里有目录中不存在的编号")
        self.assertEqual(expected - listed, set(), "目录里有文档未列出的编号")

    def test_document_states_match_the_catalog(self):
        text = DOC.read_text(encoding="utf-8")
        rows = {}
        for line in text.splitlines():
            match = re.match(r"\|\s*([A-Z]{2,3}-\d{2})\s*\|", line)
            if match:
                rows[match.group(1)] = line
        for entry in CATALOG:
            line = rows.get(entry.code)
            self.assertIsNotNone(line, entry.code)
            self.assertIn(entry.status, line, f"{entry.code} 的状态与目录不一致")


def _probe(item, want_max):
    """A measured value that lands in the lowest/highest scoring rule."""
    best, best_score = None, None
    for entry in item["thresholds"]:
        score = entry["score"] * item["weight"]
        if best_score is None or (
            score > best_score if want_max else score < best_score
        ):
            best, best_score = entry, score
    if "gte" in best:
        return best["gte"] + 1
    if "lt" in best:
        return best["lt"] - 1
    return 0


class ScaleRangeAuditTests(unittest.TestCase):
    """Every delivered scale's declared range must be its reachable range.

    The engine validates that a submitted total falls inside
    ``aggregate.min/max``, but it cannot know whether the items can
    actually reach those bounds. A pack whose weights top out below
    ``max`` would pass load-time validation and then reject the highest
    possible answer at run time. This audit closes that gap.
    """

    def test_reachable_range_equals_declared_range(self):
        from medagent.extensions.scale_engine import run_scale, validate_scale

        root = resources.files("medagent.extensions").joinpath("packs")
        checked = 0
        for file in root.iterdir():
            if not file.name.endswith(".json"):
                continue
            manifest = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(manifest.get("scale"), dict):
                continue
            spec = validate_scale(manifest)
            low, high = {}, {}
            reachable_min = reachable_max = 0
            for item in spec["items"]:
                if item["kind"] != "choice":
                    # A threshold table declares its own score range; the
                    # probe below picks a measured value that lands in the
                    # lowest and highest scoring rule.
                    scores = [e["score"] * item["weight"] for e in item["thresholds"]]
                    reachable_min += min(scores)
                    reachable_max += max(scores)
                    low[item["id"]] = _probe(item, want_max=False)
                    high[item["id"]] = _probe(item, want_max=True)
                    continue
                scores = {}
                for option in item["options"]:
                    base = (
                        item["reverse_of"] - option["value"]
                        if item["reverse_of"] is not None
                        else option["value"]
                    )
                    scores[option["value"]] = base * item["weight"]
                low_value = min(scores, key=lambda v: scores[v])
                high_value = max(scores, key=lambda v: scores[v])
                low[item["id"]] = low_value
                high[item["id"]] = high_value
                reachable_min += scores[low_value]
                reachable_max += scores[high_value]
            self.assertEqual(
                reachable_min, spec["min"], f"{manifest['id']} 可达最低分与声明不符"
            )
            self.assertEqual(
                reachable_max, spec["max"], f"{manifest['id']} 可达最高分与声明不符"
            )
            # The extreme answers must not be rejected at run time.
            run_scale(spec, low)
            run_scale(spec, high)
            checked += 1
        self.assertGreater(checked, 0)


WORKSHEET = ROOT / "docs" / "量表与公式临床评审工作表.md"


class ReviewWorksheetTests(unittest.TestCase):
    """The worksheet handed to clinicians must cover every delivered item.

    It is generated, but generation is a manual step — this test is what
    catches a pack that was added without regenerating the worksheet.
    """

    def test_worksheet_exists(self):
        self.assertTrue(WORKSHEET.is_file(), f"缺少评审工作表：{WORKSHEET}")

    def test_every_delivered_item_is_listed(self):
        text = WORKSHEET.read_text(encoding="utf-8")
        listed = set(re.findall(r"^### ([A-Z]{2,3}-\d{2}) ", text, re.M))
        expected = {entry.code for entry in CATALOG if entry.status == "delivered"}
        self.assertEqual(expected - listed, set(), "有已交付项目未列入评审工作表")
        self.assertEqual(listed - expected, set(), "工作表里有非已交付项目")

    def test_every_rejected_item_is_listed(self):
        text = WORKSHEET.read_text(encoding="utf-8")
        listed = set(re.findall(r"^\| ([A-Z]{2,3}-\d{2}) \|", text, re.M))
        expected = {entry.code for entry in CATALOG if entry.status == "rejected"}
        self.assertEqual(expected - listed, set(), "有不做项目未列入附录")


class CapabilitySelectionTests(unittest.TestCase):
    """The advertisement is what the model chooses from.

    With ~60 patient-facing capabilities, two sharing an alias means the
    model has no way to tell them apart. This is the cheap, deterministic
    half of selection quality; the other half needs a real provider.
    """

    def setUp(self):
        # Unit conversions use a fixed field policy, not aliases; only
        # scales and formulas are matched by name.
        self.patient = [
            item
            for item in load_registry().descriptions()
            if isinstance(item.get("scale"), dict)
            or isinstance(item.get("formula"), dict)
        ]

    def test_every_capability_has_aliases(self):
        for item in self.patient:
            self.assertTrue(item.get("aliases"), item["id"])

    def test_aliases_are_unique_across_capabilities(self):
        seen = {}
        for item in self.patient:
            for alias in item.get("aliases") or []:
                key = alias.lower()
                self.assertNotIn(
                    key,
                    seen,
                    f"{alias!r} 同时属于 {seen.get(key)} 与 {item['id']}",
                )
                seen[key] = item["id"]

    def test_aliases_are_short_enough_to_match(self):
        for item in self.patient:
            for alias in item.get("aliases") or []:
                self.assertLessEqual(len(alias), 40, item["id"])


SELECTION_CASES = ROOT / "tests" / "fixtures" / "capability_selection_cases.json"


class SelectionCasesTests(unittest.TestCase):
    """The selection case set must stay valid as the catalog changes.

    A case pointing at a removed capability, or a duplicated id, would
    silently corrupt every future baseline run.
    """

    def setUp(self):
        self.cases = json.loads(SELECTION_CASES.read_text(encoding="utf-8"))["cases"]
        self.known = {item["id"] for item in load_registry().descriptions()}

    def test_ids_are_unique(self):
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_expected_ids_exist(self):
        for case in self.cases:
            if case["expected"] is None:
                continue
            self.assertIn(case["expected"], self.known, case["id"])

    def test_every_case_has_an_utterance(self):
        for case in self.cases:
            self.assertTrue(case["utterance"].strip(), case["id"])

    def test_case_set_covers_negatives_and_guards(self):
        # A set made only of "patient named the scale" cases would score
        # 100% trivially; keep at least a few that must decline.
        negatives = [case for case in self.cases if case["expected"] is None]
        self.assertGreaterEqual(len(negatives), 5)


PACKET = ROOT / "docs" / "review-packets" / "explicit-calculation-v1"


class ReviewPacketTests(unittest.TestCase):
    """A signature is on a content hash, so the packet must stay current.

    If a pack in the slice changes and the packet is not regenerated, the
    hash on the signed page no longer describes the live content. This
    test is the only thing that catches that.
    """

    def setUp(self):
        self.snapshot = json.loads((PACKET / "packet.json").read_text(encoding="utf-8"))
        self.manifests = {
            item["id"]: item for item in load_registry().descriptions()
        }

    def test_packet_lists_twelve_items(self):
        self.assertEqual(len(self.snapshot["items"]), 12)

    def test_every_page_exists(self):
        for entry in self.snapshot["items"]:
            page = PACKET / f"{entry['code']}.md"
            self.assertTrue(page.is_file(), entry["code"])

    def test_content_hashes_match_live_packs(self):
        import hashlib

        for entry in self.snapshot["items"]:
            manifest = self.manifests.get(entry["id"])
            self.assertIsNotNone(manifest, entry["id"])
            canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
            digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
            self.assertEqual(
                digest,
                entry["content_sha256"],
                f"{entry['id']} 内容已变化，需重新生成送审包",
            )

    def test_every_item_is_registered_and_unsignoff(self):
        for entry in self.snapshot["items"]:
            manifest = self.manifests[entry["id"]]
            self.assertEqual(
                manifest["review_status"], "engineering_verified_not_clinical"
            )


PATIENT_FIXTURE = ROOT / "tests" / "fixtures" / "patient-capabilities.json"


class PatientSweepFixtureTests(unittest.TestCase):
    """The UI sweep iterates a generated fixture, not the live registry.

    If a capability is added or removed and the fixture is not
    regenerated, the sweep silently keeps passing on a stale list. This
    is the only thing that notices.
    """

    def setUp(self):
        self.rows = json.loads(PATIENT_FIXTURE.read_text(encoding="utf-8"))
        # The sweep drives scale and formula cards; unit conversion has
        # its own specs, so the fixture holds only those two kinds.
        self.patient = {
            item["id"]: item
            for item in load_registry().descriptions()
            if item.get("patient_form")
            and (isinstance(item.get("scale"), dict) or isinstance(item.get("formula"), dict))
        }

    def test_fixture_matches_the_registry(self):
        self.assertEqual({row["id"] for row in self.rows}, set(self.patient))

    def test_sizes_match_the_packs(self):
        for row in self.rows:
            manifest = self.patient[row["id"]]
            if row["kind"] == "scale":
                self.assertEqual(row["size"], len(manifest["scale"]["items"]), row["id"])
            elif row["kind"] == "formula":
                self.assertEqual(
                    row["size"], len(manifest["formula"]["inputs"]), row["id"]
                )

    def test_every_formula_row_carries_a_sample(self):
        for row in self.rows:
            if row["kind"] != "formula":
                continue
            self.assertTrue(row.get("sample"), row["id"])
            self.assertEqual(len(row["sample"]), row["size"], row["id"])


if __name__ == "__main__":
    unittest.main()
