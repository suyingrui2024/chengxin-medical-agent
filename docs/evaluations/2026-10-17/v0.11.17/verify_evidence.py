"""Check frozen rendering evidence and hashes without a model or download."""

import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source"


def read(name):
    return json.loads((HERE / name).read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sources = read("source-files.json")
    for name, expected in sources.items():
        assert sha(SOURCE / name) == expected, name
    for name, expected in read("source-before-mono.json").items():
        path = HERE / "before-mono-source" / name
        if not path.is_file():
            path = SOURCE / name
        assert sha(path) == expected, ("before-mono", name)
    base = read("baseline-runtime-files.json")
    for name, expected in base.items():
        assert sha(HERE / "baseline-runtime" / name) == expected, name
    assert len(base) == 115
    for name, overlay, bundled in [
        ("without-cjk", "before-fonts-source", False),
        ("bundled-body-first", "before-illustrations-source", True),
        ("final-debian", "before-mono-source", True),
        ("without-mono-fallback", "mono-probe-source", True),
        ("final-font-chains", "source", True),
    ]:
        identity = read(name + "/identity.json")
        for filename, field in [
            ("verify_clean_desktop.py", "launcher_sha256"),
            ("clean-desktop-probe.mjs", "driver_sha256"),
        ]:
            assert sha(HERE / overlay / "scripts" / filename) == identity[field]
        if bundled:
            assert (
                sha(HERE / overlay / "scripts/check-bundled-fonts.mjs")
                == identity["bundled_font_probe_sha256"]
            )
        assert identity["external_network_namespace_shared"] is False
        assert identity["development_checkout_mounted"] is False
        assert identity["host_system_libraries_mounted"] is False
        assert identity["model_requests"] == 0
    negative = read("without-cjk/probe.json")
    assert not negative["passed"]
    assert len(negative["checks"]) == 16
    assert [key for key, value in negative["checks"].items() if not value] == [
        "chinese_ui_glyph_coverage"
    ]
    assert all(len(f["sample"]) == len(f["missing"]) == 246 for f in negative["fonts"])
    assert read("bundled-body-first/probe.json")["passed"]
    prior = read("final-debian/probe.json")
    assert prior["passed"] and len(prior["bundled_fonts"]) == 2
    mono_negative = read("without-mono-fallback/probe.json")
    assert not mono_negative["passed"]
    assert "Bundled face did not load for mono" in mono_negative["error"]
    assert [f["role"] for f in mono_negative["bundled_fonts"]] == [
        "body",
        "serif",
        "mono",
    ]
    assert mono_negative["bundled_fonts"][-1]["loaded"] == []
    assert not mono_negative["bundled_fonts"][-1]["platform_fonts"][0]["isCustomFont"]
    final = read("final-font-chains/probe.json")
    assert (
        final["passed"] and len(final["checks"]) == 17 and all(final["checks"].values())
    )
    assert final["system"]["source"] == "separate-rootfs"
    assert "ID=debian" in final["system"]["os_release"]
    assert final["runtime"]["version"].startswith("3.11.")
    assert final["runtime"]["interfaces"] == ["lo"]
    assert final["runtime"]["external_connect_errno"] != 0
    assert final["application"]["version"] == "0.11.17"
    assert "--no-sandbox" in final["application"]["argv"]
    assert [f["role"] for f in final["bundled_fonts"]] == ["body", "serif", "mono"]
    for font in final["bundled_fonts"]:
        assert len(font["sample"]) == 246
        assert sum(f["glyphCount"] for f in font["platform_fonts"]) == 246
        assert all(f["isCustomFont"] for f in font["platform_fonts"])
    assert len(final["illustrations"]) == 2
    assert sum(len(f["vector_labels"]) for f in final["illustrations"]) == 17
    for image in final["illustrations"]:
        assert image["vector_labels"] == image["expected_labels"]
        assert image["font_dependent_nodes"] == 0 and image["paths"] > 0
        assert image["external_references"] == []
    assert not (HERE / "system-chinese-fonts.txt").read_bytes()
    assert not (HERE / "dpkg-final-audit.txt").read_bytes()
    font_data = json.loads((SOURCE / "src/medagent/web/font-sources.json").read_text())
    web = SOURCE / "src/medagent/web"
    assert font_data["commit"] == "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
    assert sha(web / font_data["license_file"]) == font_data["license_sha256"]
    for entry in font_data["files"]:
        data = (web / entry["file"]).read_bytes()
        assert (
            len(data) == entry["bytes"]
            and hashlib.sha256(data).hexdigest() == entry["sha256"]
        )
        assert (
            hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            == entry["git_blob_sha1"]
        )
        assert entry["modified"] is False
    labels = json.loads((web / "illustration-labels.json").read_text())
    ns = {"s": "http://www.w3.org/2000/svg"}
    for entry in labels["files"]:
        original = SOURCE / entry["editable_source"]
        assert sha(original) == entry["source_sha256"]
        assert sha(web / entry["file"]) == entry["output_sha256"]
        text = [
            e.text
            for e in ET.fromstring(original.read_bytes()).findall(".//s:text", ns)
        ]
        assert text == entry["labels"]
        svg = ET.fromstring((web / entry["file"]).read_bytes())
        assert not svg.findall(".//s:text", ns)
        assert [
            e.attrib["aria-label"] for e in svg.findall(".//s:g[@aria-label]", ns)
        ] == text
    for candidate in [
        "package-before-native.json",
        "before-mono-package-final-before-native.json",
        "before-mono-package-post-test.json",
        "package-final-before-native.json",
        "package-post-test.json",
    ]:
        package = read(candidate)
        assert (
            package["passed"] and package["locked_production_packages_checked"] == 544
        )
        for name, expected in package["source_sha256"].items():
            overlays = []
            if candidate == "package-before-native.json":
                overlays.append("before-illustrations-source")
            if (
                candidate.startswith("before-mono-")
                or candidate == "package-before-native.json"
            ):
                overlays.append("before-mono-source")
            overlays.append("source")
            path = next(
                HERE / directory / name
                for directory in overlays
                if (HERE / directory / name).is_file()
            )
            assert sha(path) == expected, (candidate, name)
    assert read("package-post-test.json")["source_files_checked"] == 120
    for name, count in [
        ("ui-mono.log", 74),
        ("native-source-mono.log", 36),
        ("native-package-mono.log", 36),
    ]:
        assert re.search(
            rf"\b{count} passed\b", (HERE / "logs" / name).read_text()
        ), name
    for directory in ["native-source-final", "native-package-final"]:
        proof = read(directory + "/font-proof.json")
        assert proof["passed"]
        for phase in ["startup", "patient"]:
            assert [font["role"] for font in proof[phase]] == ["body", "serif", "mono"]
            for font in proof[phase]:
                expected = (
                    "NotoSerifCJKsc-Regular"
                    if font["role"] == "serif"
                    else "NotoSansCJKsc-Regular"
                )
                assert all(
                    face["isCustomFont"] and face["postScriptName"] == expected
                    for face in font["platform_fonts"]
                )
    log = (HERE / "logs/backend-mono.log").read_text()
    assert "Ran 398 tests" in log and "OK (skipped=54)" in log
    frozen = read("frozen-verification.json")
    assert frozen["passed"] and frozen["tests_run"] == 220
    assert frozen["source_files_checked"] == len(sources)
    previous = {}
    for name in ["v0.11.16", "v0.11.16-clean-environment"]:
        directory = HERE.parent / name
        manifest = json.loads((directory / "archive-manifest.json").read_text())
        for path, expected in manifest["files"].items():
            assert sha(directory / path) == expected, (name, path)
        previous[name] = manifest["file_count"]
    print(
        json.dumps(
            {
                "passed": True,
                "frozen_source_files": len(sources),
                "baseline_runtime_files": len(base),
                "final_debian_assertions": 17,
                "actual_han_glyphs_per_role": 246,
                "font_roles": ["body", "serif", "mono"],
                "outlined_image_labels": 17,
                "external_model_requests": 0,
                "previous_archives_unchanged": previous,
                "scope": "Recorded bytes and rendering evidence; no new-machine, clinical or process-sandbox effectiveness claim.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
