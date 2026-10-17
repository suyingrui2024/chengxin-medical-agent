"""Fetch a preregistered TEST sample and pinned wheels, without importing upstream code."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from prepare_public_execution import extract_source

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "benchmarks/swe-lite-test-sample"
CACHE = ROOT / ".runtime/public-test-sample"
WHEELS = {
    "pytest-mock": "3.11.1",
    "mpmath": "1.2.1",
    "Jinja2": "3.0.3",
    "MarkupSafe": "2.1.3",
    "docutils": "0.16",
    "Pygments": "2.13.0",
    "snowballstemmer": "2.2.0",
    "Babel": "2.11.0",
    "packaging": "21.3",
    "pyparsing": "3.0.9",
    "alabaster": "0.7.13",
    "imagesize": "1.4.1",
    "sphinxcontrib-applehelp": "1.0.2",
    "sphinxcontrib-devhelp": "1.0.2",
    "sphinxcontrib-htmlhelp": "1.0.3",
    "sphinxcontrib-jsmath": "1.0.1",
    "sphinxcontrib-qthelp": "1.0.3",
    "sphinxcontrib-serializinghtml": "1.1.5",
    "Werkzeug": "2.3.3",
    "itsdangerous": "2.1.2",
    "click": "8.1.3",
    "blinker": "1.6.2",
    "python-dotenv": "1.0.0",
    "importlib-metadata": "6.6.0",
    "zipp": "3.15.0",
    "matplotlib": "3.7.1",
    "contourpy": "1.0.7",
    "cycler": "0.11.0",
    "fonttools": "4.39.3",
    "kiwisolver": "1.4.4",
    "Pillow": "9.5.0",
    "python-dateutil": "2.8.2",
    "six": "1.16.0",
    "pytz": "2022.7.1",
    "Jinja2@3.1.2": "3.1.2",
    "pandas": "1.5.3",
    "html5lib": "1.1",
    "webencodings": "0.5.1",
    "pytest": "7.4.4",
    "pluggy": "1.3.0",
    "iniconfig": "2.0.0",
    "exceptiongroup": "1.1.3",
    "tomli": "2.0.1",
}
EXTRA_V3 = {
    "setuptools": "65.5.1",
    "requests": "2.28.2",
    "urllib3": "1.26.15",
    "charset-normalizer": "2.0.12",
    "idna": "3.4",
    "certifi": "2022.12.7",
    "typing_extensions": "4.7.1",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, path, maximum, expected=None):
    if path.exists():
        data = path.read_bytes()
    else:
        if not url.startswith("https://"):
            raise ValueError("Only HTTPS downloads")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            print(
                json.dumps({"downloading": path.name, "maximum_seconds": 120}),
                flush=True,
            )
            result = subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--connect-timeout",
                    "20",
                    "--max-time",
                    "120",
                    "--max-filesize",
                    str(maximum),
                    "--output",
                    str(temporary),
                    url,
                ],
                stderr=subprocess.DEVNULL,
                timeout=125,
            )
            if result.returncode:
                print(
                    json.dumps(
                        {
                            "transport_fallback": path.name,
                            "curl_exit_code": result.returncode,
                        }
                    ),
                    flush=True,
                )
                fallback = """import sys
from urllib.request import urlopen
with urlopen(sys.argv[1],timeout=20) as response,open(sys.argv[2],'wb') as output:
 total=0
 while True:
  data=response.read(1048576)
  if not data:break
  total+=len(data)
  if total>int(sys.argv[3]):raise ValueError('Download exceeds bound')
  output.write(data)
"""
                second = subprocess.run(
                    [sys.executable, "-c", fallback, url, str(temporary), str(maximum)],
                    stderr=subprocess.DEVNULL,
                    timeout=125,
                )
                if second.returncode:
                    raise RuntimeError(
                        "Both bounded transports failed: "
                        + str(result.returncode)
                        + "/"
                        + str(second.returncode)
                    )
            with temporary.open("rb") as stream:
                data = stream.read(maximum + 1)
            if len(data) > maximum or expected and sha(data) != expected:
                raise ValueError("Downloaded size/hash mismatch")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if len(data) > maximum or expected and sha(data) != expected:
        raise ValueError("Cache size/hash mismatch")
    return data


def wheel(files):
    candidates = []
    for row in files:
        name = row["filename"]
        if not name.endswith(".whl") or row.get("yanked"):
            continue
        if name.endswith("py3-none-any.whl") or name.endswith("py2.py3-none-any.whl"):
            candidates.append((0, name, row))
        elif "-cp310-" in name and "manylinux" in name and "x86_64" in name:
            candidates.append((1, name, row))
    if not candidates:
        raise ValueError("No pinned cp310 Linux x64 wheel")
    return min(candidates, key=lambda row: row[:2])[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--revision", default="v3", choices=["v2", "v3"])
    args = parser.parse_args()
    selection = json.loads((FIXTURES / "selection.json").read_text())
    versions = {**WHEELS, **(EXTRA_V3 if args.revision == "v3" else {})}
    if not args.download:
        print(
            json.dumps(
                {
                    "selection": selection["selected_ids"],
                    "wheels": versions,
                    "model_calls": 0,
                }
            )
        )
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    plan = {
        "kind": "public-test-environment-inputs/v1",
        "selection_sha256": sha((FIXTURES / "selection.json").read_bytes()),
        "wheel_versions": versions,
        "cases": {},
        "wheels": {},
        "limits": {
            "compressed_file_bytes": 80000000,
            "source_expanded_bytes": 150000000,
        },
    }
    plan_path = CACHE / f"inputs-{args.revision}.json"
    if plan_path.exists():
        previous = json.loads(plan_path.read_text())
        if (
            previous["selection_sha256"] != plan["selection_sha256"]
            or previous["wheel_versions"] != versions
        ):
            raise ValueError("Input plan changed")
        plan = previous

    def save():
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")

    save()
    failures = []

    def prepare_source(identity):
        instance = json.loads((FIXTURES / identity / "instance.json").read_text())
        url = f"https://codeload.github.com/{instance['repo']}/tar.gz/{instance['base_commit']}"
        target = CACHE / "archives" / (identity + ".tar.gz")
        data = fetch(
            url, target, 80000000, plan["cases"].get(identity, {}).get("sha256")
        )
        directory = CACHE / "sources" / identity
        if not directory.exists():
            import tempfile

            directory.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                dir=directory.parent, prefix="extract-"
            ) as raw:
                staged = Path(raw) / "source"
                staged.mkdir()
                extract_source(data, staged)
                staged.replace(directory)
        plan["cases"][identity] = {
            "url": url,
            "sha256": sha(data),
            "bytes": len(data),
            "source": str(directory.relative_to(ROOT)),
        }
        save()
        print(json.dumps({"source": identity, "bytes": len(data)}), flush=True)

    for identity in selection["selected_ids"]:
        try:
            prepare_source(identity)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            record = {
                "source": identity,
                "status": "download_or_extract_failed",
                "error_type": type(error).__name__,
                "message": str(error)[:300],
            }
            failures.append(record)
            plan.setdefault("failure_history", []).append(record)
            save()
            print(json.dumps(record), flush=True)

    def prepare_wheel(key, version):
        project = key.split("@")[0]
        url = f"https://pypi.org/pypi/{project}/{version}/json"
        data = fetch(url, CACHE / "metadata" / f"{project}-{version}.json", 5000000)
        metadata = json.loads(data)
        item = wheel(metadata["urls"])
        content = fetch(
            item["url"],
            CACHE / "wheels" / item["filename"],
            80000000,
            item["digests"]["sha256"],
        )
        return {
            "project": project,
            "version": version,
            "filename": item["filename"],
            "url": item["url"],
            "sha256": sha(content),
            "bytes": len(content),
            "license": metadata["info"].get("license"),
            "requires_dist": metadata["info"].get("requires_dist"),
            "metadata_url": url,
        }

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(prepare_wheel, key, version): key
            for key, version in versions.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                item = future.result()
                plan["wheels"][key] = item
                save()
                print(
                    json.dumps(
                        {
                            "wheel": key,
                            "version": item["version"],
                            "bytes": item["bytes"],
                        }
                    ),
                    flush=True,
                )
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.TimeoutExpired,
            ) as error:
                record = {
                    "wheel": key,
                    "status": "download_failed",
                    "error_type": type(error).__name__,
                }
                failures.append(record)
                plan.setdefault("failure_history", []).append(record)
                save()
                print(json.dumps(record), flush=True)
    if failures:
        raise SystemExit(1)
    if args.revision == "v3":
        # Fixed upstream artifact; the historical interpreter only executes inside bwrap.
        name = "cpython-3.9.20+20241002-x86_64-unknown-linux-gnu-install_only.tar.gz"
        url = (
            "https://github.com/astral-sh/python-build-standalone/releases/download/20241002/"
            + name.replace("+", "%2B")
        )
        expected = "fec849569158e5e46610621d7da91b02dca2cc5f66dd49cc0f85d7469c8f5a84"
        checksum = (
            fetch(url + ".sha256", CACHE / "python" / (name + ".sha256"), 1000)
            .decode()
            .split()[0]
        )
        if checksum != expected:
            raise ValueError("Historical Python publisher checksum changed")
        content = fetch(url, CACHE / "python" / name, 30000000, expected)
        (CACHE / "python39.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "version": "3.9.20",
                    "release_tag": "20241002",
                    "url": url,
                    "sha256_url": url + ".sha256",
                    "sha256": expected,
                    "bytes": len(content),
                    "scope": "Historical Sphinx test environment only; not the product interpreter.",
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
