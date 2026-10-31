"""Emit the patient-facing capability list the UI smoke test iterates.

The spec needs the list at load time, before any server call. Generating
it here keeps the test honest: `tests/test_catalog.py` asserts the file
still matches the registry, so a capability added or removed without
regenerating turns the suite red instead of silently shrinking coverage.
"""

import json
from pathlib import Path

from medagent.extensions.builtin import load_registry
from medagent.extensions.formula_engine import run_formula, validate_formula

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "fixtures" / "patient-capabilities.json"
OUT_CLINICIAN = ROOT / "tests" / "fixtures" / "clinician-capabilities.json"

def _sample(manifest):
    """A set of inputs the engine actually accepts.

    Filling every field with the midpoint of its own range looks
    reasonable and is wrong for anything additive: nine 0-100 burn-area
    inputs give 450, far outside the declared 0-100 output. So try a few
    fractions and keep the first that produces an in-range result.
    """
    spec = validate_formula(manifest)
    inputs = spec["inputs"]
    for fraction in (0.1, 0.25, 0.5, 0.0):
        values = {}
        for entry in inputs:
            if entry["type"] == "enum":
                values[entry["id"]] = entry["options"][0]["value"]
            elif entry["type"] == "date":
                values[entry["id"]] = entry["min"]
            else:
                low, high = float(entry["min"]), float(entry["max"])
                values[entry["id"]] = f"{low + (high - low) * fraction:.4f}"
        try:
            run_formula(spec, values)
            return values
        except Exception:  # noqa: BLE001 - any rejection means try the next fraction
            continue
    raise SystemExit(f"{manifest['id']}: 找不到合法输入样例")


rows = []
for item in load_registry().descriptions():
    if not (isinstance(item.get("scale"), dict) or isinstance(item.get("formula"), dict)):
        continue
    inputs = None
    sample = None
    if isinstance(item.get("scale"), dict):
        kind = "scale"
        size = len(item["scale"]["items"])
        sample = {}
        for entry in item["scale"]["items"]:
            if entry["kind"] == "choice":
                sample[entry["id"]] = entry["options"][0]["value"]
            else:
                # Threshold items take a measured value, not an option.
                best = min(entry["thresholds"], key=lambda e: e["score"])
                if "gte" in best:
                    sample[entry["id"]] = best["gte"] + 1
                elif "lt" in best:
                    sample[entry["id"]] = max(best["lt"] - 1, 0)
    elif isinstance(item.get("formula"), dict):
        kind = "formula"
        size = len(item["formula"]["inputs"])
        inputs = [
            {
                "type": entry["type"],
                "min": entry.get("min"),
                "max": entry.get("max"),
            }
            for entry in item["formula"]["inputs"]
        ]
        sample = _sample(item)
    else:
        kind = "conversion"
        size = len(item.get("input_schema", {}).get("properties", {}))
    rows.append(
        {
            "id": item["id"],
            "audience": item.get("audience"),
            "name": item["name"],
            "kind": kind,
            "size": size,
            "aliases": list(item.get("aliases") or []),
            "inputs": inputs,
            "sample": sample,
        }
    )

rows.sort(key=lambda row: row["id"])
patient = [row for row in rows if row["audience"] != "clinician"]
clinician = [row for row in rows if row["audience"] == "clinician"]
OUT.write_text(json.dumps(patient, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
OUT_CLINICIAN.write_text(
    json.dumps(clinician, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"wrote {OUT} entries={len(patient)}")
print(f"wrote {OUT_CLINICIAN} entries={len(clinician)}")
