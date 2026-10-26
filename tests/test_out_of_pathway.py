"""P3 off-pathway flow (docs/假设驱动问诊设计文档.md §4): content + module.

Contract under test (frozen for the P0 sorter):
- ``warning_signs.json``: version 1.0.0, exactly the six danger-signal
  domains 意识/呼吸/出血/剧痛/持续加重/功能骤降, question text with
  no organ-specific wording.
- ``warning_questions(chief)`` returns the full domain list if and only
  if ``chief_matches_pathway(chief)`` is False, else [].
- ``SCOPE_NOTE`` is the verbatim scope-disclosure text. Wiring it into
  the close message is P2's job in ``_record_reply``; this file asserts
  the constant text and the ``warning_questions`` behaviour only.
  Close-wiring verification is deferred to P2 landing + supervisor
  re-check (see the P3 report).

Off-pathway fixtures use the verbatim second-round ``chief_complaint``
fact values from ~/workspace/live-eval/disease-round2:
- K3 牙龈出血: "刷牙总出血，牙龈红肿，咬苹果也出血"
- Y3 麦粒肿: "眼皮上长了个红肿的小包，碰着疼"
- E3 肥胖代谢: "体重增加、肚子大、爬楼就喘" -- see
  ``test_e3_verbatim_chief_triggers_pathway_gate`` for why this one
  does NOT receive warning questions through the frozen gate.
"""

from __future__ import annotations

import json
from importlib import resources

from medagent import conclusion as conclusion_module
from medagent.conclusion import apply_neutral_fallback, chief_matches_pathway
from medagent.out_of_pathway import SCOPE_NOTE, warning_questions

EXPECTED_DOMAINS = ["意识", "呼吸", "出血", "剧痛", "持续加重", "功能骤降"]

# Test-side oracle for "organ-specific": body-part / organ words whose
# presence in a warning question or in the scope note would mean the
# text borrowed a pathway's organ framing. "血" is deliberately NOT
# listed: it is the 出血 domain itself, not an organ. "指" is not
# listed either: "指征" in SCOPE_NOTE is generic triage wording, not a
# finger reference.
ORGAN_SPECIFIC_TERMS = [
    "眼", "牙", "齿", "皮肤", "腰", "腹", "胸", "头", "颈",
    "腿", "脚", "手", "耳", "鼻", "喉", "口", "肺", "心",
    "胃", "肠", "肝", "肾", "膀胱", "关节", "骨", "视力",
    "听力", "尿", "便", "嘴", "舌", "脸", "背", "肩", "肚",
    "咽", "嗓", "脑", "趾", "腕", "踝",
]

# Verbatim second-round chief_complaint values (see module docstring).
K3_CHIEF = "刷牙总出血，牙龈红肿，咬苹果也出血"
Y3_CHIEF = "眼皮上长了个红肿的小包，碰着疼"
E3_CHIEF = "体重增加、肚子大、爬楼就喘"


def _load_content(name):
    raw = resources.files("medagent.content").joinpath(name).read_text(encoding="utf-8")
    return json.loads(raw)


def _collect_strings(node, key=None):
    """Collect every string value stored under ``key`` in nested data."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, str):
                found.append(v)
            else:
                found.extend(_collect_strings(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_strings(item, key))
    return found


def test_warning_signs_schema_and_domain_order():
    data = _load_content("warning_signs.json")
    assert data["version"] == "1.0.0"
    domains = data["domains"]
    assert [d["domain"] for d in domains] == EXPECTED_DOMAINS
    for entry in domains:
        assert set(entry.keys()) == {"domain", "question"}
        assert isinstance(entry["question"], str) and entry["question"].strip()


def test_warning_questions_match_json_verbatim():
    expected = _load_content("warning_signs.json")["domains"]
    assert warning_questions(K3_CHIEF) == expected
    assert warning_questions(Y3_CHIEF) == expected


def test_questions_and_scope_note_contain_no_organ_specific_terms():
    texts = [d["question"] for d in _load_content("warning_signs.json")["domains"]]
    texts += [d["domain"] for d in _load_content("warning_signs.json")["domains"]]
    texts.append(SCOPE_NOTE)
    for text in texts:
        for term in ORGAN_SPECIFIC_TERMS:
            assert term not in text, f"organ-specific term {term!r} in {text!r}"


def test_questions_and_scope_note_contain_no_department_or_diagnosis_words():
    # Dynamic oracles (same approach as the P1 lint): department names
    # from departments.json, hypothesis labels/patient_labels from
    # differentials.json. Nothing is hardcoded, so content drift fails
    # loudly instead of passing vacuously.
    departments = _collect_strings(_load_content("departments.json"), "department")
    differentials = _load_content("differentials.json")
    labels = _collect_strings(differentials, "label")
    labels += _collect_strings(differentials, "patient_label")
    texts = [d["question"] for d in warning_questions(K3_CHIEF)] + [SCOPE_NOTE]
    for text in texts:
        for name in departments:
            assert name not in text, f"department name {name!r} in {text!r}"
        for label in labels:
            if len(label) >= 2:
                assert label not in text, f"diagnosis label {label!r} in {text!r}"


def test_off_pathway_chiefs_k3_y3():
    # Grounding: these verbatim second-round chiefs trigger no pathway.
    assert chief_matches_pathway(K3_CHIEF) is False
    assert chief_matches_pathway(Y3_CHIEF) is False
    for chief in (K3_CHIEF, Y3_CHIEF):
        got = warning_questions(chief)
        assert [d["domain"] for d in got] == EXPECTED_DOMAINS
        assert all(d["question"].strip() for d in got)


def test_e3_verbatim_chief_triggers_pathway_gate():
    """OPEN GATE ISSUE (not a P3 redefinition): E3 is an off-pathway
    disease (no endocrine pathway exists), but its recorded second-round
    chief text incidentally triggers pathways -- "肚子" matches the
    abdominal pathway and "喘" matches the respiratory pathway -- so
    ``chief_matches_pathway`` is True and the frozen
    "if and only if False" contract requires ``warning_questions`` to
    return []. P3 must not redefine the gate (conclusion.py is
    read-only for this package, and P0 will call this interface), so
    this test pins the contract behaviour and the parent/supervisor
    must decide the gate input (e.g. P0 passes a disease-core chief)
    before E3 can receive warning questions through this interface.
    """
    assert chief_matches_pathway(E3_CHIEF) is True
    assert warning_questions(E3_CHIEF) == []


def test_e3_core_chief_receives_all_warning_questions():
    # The E3 disease-core complaint, without the incidental pathway
    # words in the recorded chief text, is off-pathway and gets the
    # full domain list -- proving the module itself covers E3 once the
    # gate input question above is resolved.
    assert chief_matches_pathway("体重增加") is False
    got = warning_questions("体重增加")
    assert [d["domain"] for d in got] == EXPECTED_DOMAINS


def test_in_pathway_chiefs_return_empty():
    for chief in (
        "咳嗽两天",
        "胸痛",
        "腰痛",
        "头痛",
        "尿频尿急",
        "身上起疹子",
    ):
        assert chief_matches_pathway(chief) is True, chief
        assert warning_questions(chief) == []


def test_empty_chief_returns_all_domains():
    # chief_matches_pathway("") is False, so the contract's "if and
    # only if" reading returns the full list for a missing chief.
    assert chief_matches_pathway("") is False
    assert [d["domain"] for d in warning_questions("")] == EXPECTED_DOMAINS


def test_warning_questions_returns_fresh_copies():
    first = warning_questions(K3_CHIEF)
    first[0]["question"] = "MUTATED"
    first.append({"domain": "MUTATED", "question": "MUTATED"})
    second = warning_questions(K3_CHIEF)
    assert second == _load_content("warning_signs.json")["domains"]
    assert warning_questions(K3_CHIEF) == warning_questions(K3_CHIEF)


def test_scope_note_verbatim():
    assert SCOPE_NOTE == (
        "这次问诊没有覆盖到你这个问题的专属分诊路径，"
        "结论只给到观察与就诊指征，具体判断仍需线下评估。"
    )
    for phrase in conclusion_module.BANNED_PHRASES:
        assert phrase not in SCOPE_NOTE


def test_neutral_fallback_baseline_item_by_item():
    """Hard neutral rule, item-by-item against apply_neutral_fallback.

    A sheet that borrows pathway content (skin self-care, skin safety
    net, pathway-flavoured narrative) must come back with exactly the
    generic self-care group, the generic four-item safety net, and the
    neutral narrative block -- and no pathway-specific item may survive
    in any of those fields.
    """
    self_care = _load_content("self_care.json")
    groups = {g["id"]: g for g in self_care["groups"]}
    generic = groups["generic_routine"]
    skin = groups["skin_routine"]
    pathway_safety = self_care["return_now_if"]["by_pathway"]["skin"]

    borrowed = {
        "tier": "routine",
        "disposition": {"level": "self_care"},
        "narrative": {"framing": "皮肤不适的框定", "severity": "程度较轻"},
        "self_care": {
            "do": list(skin["do"]),
            "avoid": list(skin["avoid"]),
            "measure": list(skin["measure"]),
        },
        "return_now_if": list(pathway_safety),
        "outlook": "皮肤问题短期展望",
    }
    neutral = apply_neutral_fallback(borrowed)

    assert neutral["neutral_fallback"] is True
    # Self-care is exactly the generic group.
    assert neutral["self_care"]["do"] == list(generic["do"])
    assert neutral["self_care"]["avoid"] == list(generic["avoid"])
    assert neutral["self_care"]["measure"] == list(generic["measure"])
    # Safety net is exactly the generic four items.
    assert neutral["return_now_if"] == list(conclusion_module.RETURN_NOW_IF)
    # Narrative is exactly the neutral block for this level.
    narrative = _load_content("conclusion_narrative.json")["neutral"]
    assert neutral["narrative"]["framing"] == narrative["framing"]["self_care"]
    assert neutral["narrative"]["severity"] == narrative["severity"]["self_care"]

    # Item-by-item: no non-generic pathway item survives anywhere.
    generic_items = set(generic["do"]) | set(generic["avoid"]) | set(generic["measure"])
    survived_care = (
        neutral["self_care"]["do"]
        + neutral["self_care"]["avoid"]
        + neutral["self_care"]["measure"]
    )
    for group in self_care["groups"]:
        if group["id"] == "generic_routine":
            continue
        for item in group["do"] + group["avoid"] + group["measure"]:
            if item not in generic_items:
                assert item not in survived_care, f"pathway item survived: {item!r}"
    for pathway, items in self_care["return_now_if"]["by_pathway"].items():
        for item in items:
            if item not in conclusion_module.RETURN_NOW_IF:
                assert item not in neutral["return_now_if"], (
                    f"pathway safety item survived from {pathway}: {item!r}"
                )
    # Neutral narrative itself carries no organ-specific framing.
    rendered = neutral["narrative"]["framing"] + neutral["narrative"]["severity"]
    for term in ORGAN_SPECIFIC_TERMS:
        assert term not in rendered, f"organ term {term!r} in neutral narrative"
