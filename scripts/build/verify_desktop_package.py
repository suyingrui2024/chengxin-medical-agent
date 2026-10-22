"""Check our Linux x64 package's sources, production metadata and locked versions."""

import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(values, host):
    return not values or (
        "!" + host not in values
        and (host in values or all(value.startswith("!") for value in values))
    )


def verify(root, application):
    metadata = json.loads((root / "package.json").read_text())
    files = [
        path
        for name in ["src", "desktop", "integrations"]
        for path in (root / name).rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ] + [
        root / name
        for name in ["pyproject.toml", "README.md", "THIRD_PARTY_NOTICES.md"]
    ]
    source_errors = [
        str(path.relative_to(root))
        for path in files
        if not (application / path.relative_to(root)).is_file()
        or digest(path) != digest(application / path.relative_to(root))
    ]
    expected_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"scripts", "devDependencies", "private"}
    }
    metadata_matches = (
        json.loads((application / "package.json").read_text()) == expected_metadata
    )
    lock = json.loads((root / "package-lock.json").read_text())["packages"]
    versions, excluded = {}, []
    for name, entry in lock.items():
        if not name or entry.get("dev"):
            continue
        if not matches(entry.get("os"), "linux") or not matches(
            entry.get("cpu"), "x64"
        ):
            excluded.append(
                {
                    "path": name,
                    "optional": entry.get("optional", False),
                    "os": entry.get("os"),
                    "cpu": entry.get("cpu"),
                }
            )
            continue
        file = application / name / "package.json"
        actual = json.loads(file.read_text()).get("version") if file.is_file() else None
        versions[name] = {"expected": entry.get("version"), "actual": actual}
    version_errors = [
        name for name, entry in versions.items() if entry["expected"] != entry["actual"]
    ]
    binary = application.parent.parent / "Chengxin"
    return {
        "version": metadata["version"],
        "platform": "linux-x64",
        "source_files_checked": len(files),
        "source_sha256": {str(path.relative_to(root)): digest(path) for path in files},
        "source_mismatches": source_errors,
        "production_metadata_matches_expected_packager_reduction": metadata_matches,
        "locked_production_packages_checked": len(versions),
        "package_version_mismatches": version_errors,
        "platform_inapplicable_optional_packages": excluded,
        "binary_sha256": digest(binary),
        "scope": "Version and own-source verification; not a full transitive dependency integrity audit.",
        "passed": not source_errors
        and not version_errors
        and metadata_matches
        and all(entry["optional"] for entry in excluded),
    }


def main():
    root = Path(__file__).resolve().parents[2]
    metadata = json.loads((root / "package.json").read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--application",
        type=Path,
        default=root
        / f"dist/desktop/Chengxin-{metadata['version']}-linux-x64/resources/app",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(root, args.application.resolve())
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key
                not in {"source_sha256", "platform_inapplicable_optional_packages"}
            }
        )
    )
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
