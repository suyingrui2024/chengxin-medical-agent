"""Recompute the frozen draw with the exposure set recorded before selection."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE / "snapshot"
selection = json.loads(
    (ROOT / "benchmarks/swe-lite-transfer/selection.json").read_text()
)
rows_path = HERE / "dataset/test-rows.json"
assert (
    hashlib.sha256(rows_path.read_bytes()).hexdigest() == selection["rows_json_sha256"]
)
assert (
    hashlib.sha256((HERE / "dataset/test.parquet").read_bytes()).hexdigest()
    == selection["dataset_parquet_sha256"]
)
source = ROOT / "scripts/select_public_transfer.py"
expected = json.loads((HERE / "source-files.json").read_text())[
    str(source.relative_to(ROOT))
]
assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
spec = importlib.util.spec_from_file_location("frozen_transfer_selection", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
chosen, sizes = module.select(
    json.loads(rows_path.read_text()),
    set(selection["excluded_previous_instance_ids"]),
    selection["seed"],
)
assert [row["instance_id"] for row in chosen] == selection["selected_ids"]
assert sizes == selection["eligible_counts_after_exclusions"]
for row in chosen:
    identity = row["instance_id"]
    assert (
        hashlib.sha256(
            json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        == selection["instances_sha256"][identity]
    )
    assert (
        json.loads(
            (
                ROOT / "benchmarks/swe-lite-transfer" / identity / "instance.json"
            ).read_text()
        )
        == row
    )
print(
    json.dumps(
        {
            "frozen_draw_reproduced": True,
            "dataset_files_hashes_match": True,
            "selected_instance_rows_match": True,
            "selected_ids": selection["selected_ids"],
            "eligible_counts": sizes,
            "excluded_before_selection": len(
                selection["excluded_previous_instance_ids"]
            ),
            "scope": "Uses the frozen exposure set, not the later on-disk fixture catalog. Parquet/JSON equality independently checked in the archived dataset proof.",
            "external_model_calls": 0,
        },
        ensure_ascii=False,
        indent=2,
    )
)
