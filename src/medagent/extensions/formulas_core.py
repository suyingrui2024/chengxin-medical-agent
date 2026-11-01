"""Shared vocabulary for the trusted formula tables.

``FormulaSpec`` and the field helpers live here rather than in ``formulas`` so
that both the main table and the department-catalog slice can depend on them
without depending on each other.  Before this split, ``formulas`` merged the
slice at the bottom of its module while the slice imported ``FormulaSpec`` back
from ``formulas`` — a cycle that worked only because of import order.

Constants are ``Decimal`` literals so results are identical on every platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

DISCLAIMER = (
    "本结果由公式程序按标注来源计算，仅作记录与参考，"
    "不构成诊断，也不替代医生判断。"
)


@dataclass(frozen=True)
class FormulaSpec:
    key: str
    citation: str
    inputs: List[Dict[str, Any]]
    output: Dict[str, Any]
    method: str
    limitations: Sequence[str]
    fn: Callable[[Mapping[str, Any]], Decimal]
    render: Callable[[Mapping[str, Any], str], str]
    audit_cases: Sequence[Tuple[Dict[str, str], str]]
    disclaimer: str = DISCLAIMER


def _number(identity, label, unit, low, high, step="0.1"):
    return {
        "id": identity,
        "label": label,
        "type": "number",
        "unit": unit,
        "min": low,
        "max": high,
        "step": step,
    }


def _output(label, unit, low, high, places, direction):
    return {
        "label": label,
        "unit": unit,
        "min": low,
        "max": high,
        "precision": {"places": places},
        "direction": direction,
    }


def _bmi(values):
    height = values["height_cm"] / Decimal(100)
    return values["weight_kg"] / (height * height)


def _bsa(values):
    return (
        Decimal("0.0061") * values["height_cm"]
        + Decimal("0.0128") * values["weight_kg"]
        - Decimal("0.1529")
    )


def _map(values):
    return (values["sbp"] + Decimal(2) * values["dbp"]) / Decimal(3)


def _corrected_calcium(values):
    return values["calcium_mmol_l"] + Decimal("0.02") * (
        Decimal(40) - values["albumin_g_l"]
    )


def _anion_gap(values):
    return values["sodium"] - values["chloride"] - values["bicarbonate"]


def _osmolality(values):
    return (
        Decimal(2) * values["sodium"]
        + values["glucose_mmol_l"]
        + values["urea_mmol_l"]
    )


def _shock_index(values):
    return values["heart_rate"] / values["sbp"]


def _child_weight(values):
    return values["age_years"] * Decimal(2) + Decimal(8)
