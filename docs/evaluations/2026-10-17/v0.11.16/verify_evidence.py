"""Verify recorded bytes and evidence consistency; does not call a model."""

import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name).read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_sources(name, overlays=()):
    values = read(name)
    for relative, expected in values.items():
        path = HERE / "source" / relative
        for overlay in overlays:
            candidate = HERE / overlay / relative
            if candidate.is_file():
                path = candidate
        assert path.is_file() and digest(path) == expected, (name, relative)
    return len(values)


def main():
    counts = {}
    stages = [
        ("final", "source-files.json", ()),
        ("directed", "runtime-source-first.json", ("earlier-model-source",)),
        (
            "before_handoff_documentation",
            "source-before-handoff-documentation.json",
            ("before-handoff-documentation",),
        ),
        (
            "before_full_driver",
            "source-before-full-driver.json",
            ("earlier-evaluation-source", "earlier-documentation"),
        ),
        (
            "before_selection",
            "source-before-selected-driver.json",
            ("before-selected-case-driver", "earlier-documentation"),
        ),
        (
            "before_patient_mode",
            "source-before-patient-mode-driver.json",
            ("before-patient-mode-driver", "earlier-documentation"),
        ),
        (
            "first_full",
            "full-flow/source-v01116.json",
            ("before-selected-case-driver", "earlier-documentation"),
        ),
        (
            "selected_full",
            "full-flow/source-v01116-selected.json",
            ("before-patient-mode-driver", "earlier-documentation"),
        ),
        (
            "patient_full",
            "full-flow/source-v01116-patient.json",
            ("earlier-documentation",),
        ),
    ]
    for label, manifest, overlays in stages:
        counts[label] = check_sources(manifest, overlays)
    assert counts["final"] == 312 and counts["directed"] == 117

    reports = {}
    for label, overlays, calls in [
        ("first", ("before-selected-case-driver",), 24),
        ("selected", ("before-patient-mode-driver",), 33),
        ("patient", (), 15),
    ]:
        report = read(f"full-flow/{label}.json")
        for field in ("source_sha256", "test_files_sha256"):
            for relative, expected in report[field].items():
                path = HERE / "source" / relative
                for overlay in overlays:
                    candidate = HERE / overlay / relative
                    if candidate.is_file():
                        path = candidate
                assert path.is_file() and digest(path) == expected, (label, relative)
        assert report["attempted_calls"] == len(report["calls"]) == calls
        assert sum(c["live_calls"] for c in report["cases"]) == calls
        reports[label] = report
    assert not reports["first"]["passed"]
    assert reports["selected"]["passed"] and reports["patient"]["passed"]
    assert sum(c.get("status") == 502 for c in reports["first"]["calls"]) == 2
    assert sum(c.get("status") is None for c in reports["first"]["calls"]) == 1
    assert all(
        c.get("status") == 200
        for r in ("selected", "patient")
        for c in reports[r]["calls"]
    )
    patient = reports["patient"]["cases"][0]
    assert patient["id"] == "native_full_location_interview"
    assert patient["recovery_attempts"] == 0 and all(patient["checks"].values())
    assert patient["checks"]["patient_view_kept_through_confirmation"]
    matrix = read("full-flow-matrix.json")
    aliases = {
        "live-v01116-first.json": "first",
        "live-v01116-selected.json": "selected",
        "live-v01116-patient.json": "patient",
    }
    assert len(matrix["matrix"]) == 4
    for case_id, evidence in matrix["matrix"].items():
        case = next(
            c
            for c in reports[aliases[evidence["report"]]]["cases"]
            if c["id"] == case_id
        )
        assert case["passed"] and case["live_calls"] == evidence["calls"]
        assert case["checks"] == evidence["checks"] and all(case["checks"].values())
    assert matrix["version_16_calls"] == 72
    assert (
        matrix["cumulative_directory_calls"] == 93 <= matrix["cumulative_limit"] == 100
    )
    directed = read("live-first.json")
    assert directed["passed"] and directed["attempted_calls"] == 4
    assert len(directed["cases"]) == 2 and all(c["passed"] for c in directed["cases"])
    assert read("actual-correction-source.json")["report_sha256"] == digest(
        HERE / "live-first.json"
    )
    native = read("native-proof/measurements.json")
    assert native["passed"] and native["local_fixture_requests"] == 6
    assert native["external_provider_requests"] == 0
    assert all(m["visible"] for m in native["measurements"])
    frozen = read("final-frozen-verification.json")
    assert frozen["passed"] and frozen["source_files_checked"] == 312
    assert frozen["tests_run"] == 220 and all(frozen["checks"].values())
    for name in (
        "package-audit.json",
        "package-before-native.json",
        "package-post-test-audit.json",
    ):
        package = read(name)
        assert package["passed"] and package["source_files_checked"] == 114
        assert package["locked_production_packages_checked"] == 544
        assert package["source_sha256"] == read("package-audit.json")["source_sha256"]
    old_counts = {}
    for name in ("v0.11.15", "v0.11.15-full-flow"):
        base = HERE.parent / name
        manifest = json.loads((base / "archive-manifest.json").read_text())
        for relative, expected in manifest["files"].items():
            assert digest(base / relative) == expected, (name, relative)
        old_counts[name] = manifest["file_count"]
    print(
        json.dumps(
            {
                "passed": True,
                "source_stage_counts": counts,
                "real_v01116_attempts": 76,
                "full_flow_attempts": 72,
                "full_flow_pass_evidence_cases": 4,
                "single_clean_four_case_invocation": False,
                "patient_view_head_retries": 0,
                "old_archives_unchanged": old_counts,
                "external_model_calls_for_this_verification": 0,
                "scope": "Evidence hashes and internal consistency; no new model, UI or clinical evaluation.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
