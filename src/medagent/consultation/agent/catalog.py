"""What the model is told about the capabilities it may call.

The advertisement is deliberately thin.  With ~60 patient-facing
capabilities the full description had grown to 30 KB -- more than half the
static prefix -- because every entry repeated the version, the review
status, the patient form and a per-item call note, and formulas shipped
their input schema twice.  None of that helps the model choose; what remains
is what selection actually needs: id, name, size, and the aliases a patient
might say.

The other constants here are the vocabulary the model proposes against: the
fields the emergency screen covers, the wording that means the symptom
comes and goes rather than worsening steadily, and the task kinds the model
is allowed to spawn.
"""
from __future__ import annotations


SCREENING_FIELDS = frozenset({"associated_symptoms"})

FLUCTUATION_PHRASES = (
    "此起彼伏",
    "旧的消了新的又起",
    "新的又起",
    "消了又起",
    "新旧交替",
    "时起时伏",
    # E2E-P2-5: paroxysmal / intermittent course ("一阵一阵的，
    # 中间有缓解") is fluctuation, not steadiness — it used to
    # slip through this guard and land as stable.
    "一阵一阵",
    "一阵阵",
    "中间有缓解",
    "时好时坏",
)

MODEL_SPAWN_TASK_KINDS = frozenset({"evidence_lookup", "second_opinion"})

def _capability_advertisement(item):
    """What the model is told about one patient-facing capability.

    Kept deliberately thin. With ~60 patient-facing capabilities the
    advertisement had grown to 30 KB — more than half the static prefix —
    because every entry repeated ``version``, ``review_status``,
    ``patient_form`` and a per-item call note, and formulas shipped their
    full input schema twice. None of that helps the model choose; the
    shared facts now live once in ``capability_note``.

    What remains is what selection actually needs: id, name, size, and
    the aliases a patient might say.
    """
    base = {"id": item["id"], "name": item["name"]}
    if item.get("patient_form") == "scale_form/v1":
        scale = item.get("scale") or {}
        aggregate = scale.get("aggregate") or {}
        base["items"] = len(scale.get("items") or [])
        base["range"] = [aggregate.get("min"), aggregate.get("max")]
        base["aliases"] = list(item.get("aliases") or [])
        return base
    formula = item.get("formula")
    if isinstance(formula, dict):
        base["inputs"] = [entry["id"] for entry in formula.get("inputs") or []]
        base["unit"] = (formula.get("output") or {}).get("unit", "")
        base["aliases"] = list(item.get("aliases") or [])
        return base
    # Unit conversions keep their schema: the prompt tells the model to
    # prefill value/from_unit when the patient already said them, so it
    # needs the field names. There are only two of these.
    base["proposal_input_schema"] = {**item["input_schema"], "required": []}
    base["execution_input_schema"] = item["input_schema"]
    return base


__all__ = [
    "FLUCTUATION_PHRASES",
    "MODEL_SPAWN_TASK_KINDS",
    "SCREENING_FIELDS",
    "_capability_advertisement",
]
