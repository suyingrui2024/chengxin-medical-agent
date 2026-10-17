"""Rebuild pinned public DEV sources and dependencies; never run downloaded code."""

import argparse
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
from urllib.request import urlopen
import zipfile

from public_execution_cases import FIXTURES, ROOT, PUBLIC_RUNNER, inventory, sha256


def download(item, path, maximum):
    if path.exists():
        data = path.read_bytes()
    else:
        if not item["url"].startswith("https://"):
            raise ValueError("Only HTTPS pinned downloads are allowed")
        with urlopen(item["url"], timeout=60) as response:
            data = response.read(maximum + 1)
    if len(data) > maximum or sha256(data) != item["sha256"]:
        raise ValueError("Pinned download failed size/hash validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def safe_parts(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("Unsafe archive member")
    return path.parts


def extract_source(data, directory):
    total, count, prefix = 0, 0, None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            parts = safe_parts(member.name)
            if not parts:
                continue
            if prefix is None:
                prefix = parts[0]
            if parts[0] != prefix:
                raise ValueError("Archive must have one root")
            if member.isdir():
                continue
            # Upstream tarballs may include links; do not expose host paths.
            if not member.isfile():
                continue
            total += member.size
            count += 1
            if total > 150_000_000 or count > 20000 or len(parts) < 2:
                raise ValueError("Source archive exceeds bounds")
            path = directory.joinpath(*parts[1:])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())


def extract_wheel(data, directory):
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            parts = safe_parts(member.filename)
            if member.is_dir():
                continue
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("Wheel links are not accepted")
            total += member.file_size
            if total > 20_000_000:
                raise ValueError("Wheel exceeds expansion bound")
            path = directory.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(member))


def prepare(rebuild=False):
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    wheel_cache = ROOT / ".runtime/public-benchmark-research/dependencies"
    for item in manifest["cases"]:
        source = (ROOT / item["source_cache"]).resolve()
        source.relative_to(ROOT / ".runtime/public-benchmark-research/sources")
        if source.exists() and not rebuild:
            if inventory(source) != item["source_sha256"]:
                raise ValueError("Existing cache changed; inspect before --rebuild")
            print(json.dumps({"case": item["instance_id"], "cache_verified": True}))
            continue
        data = download(item["archive"], source.parent / "base.tar.gz", 30_000_000)
        if source.exists():
            shutil.rmtree(source)
        source.mkdir(parents=True)
        extract_source(data, source)
        for name in item["dependencies"]:
            wheel = manifest["dependencies"][name]
            content = download(wheel, wheel_cache / wheel["filename"], 3_000_000)
            extract_wheel(content, source / ".benchmark_dependencies")
        (source / "benchmark_run_tests.py").write_text(PUBLIC_RUNNER)
        if inventory(source) != item["source_sha256"]:
            raise ValueError("Rebuilt source/dependencies differ from frozen inventory")
        print(json.dumps({"case": item["instance_id"], "cache_rebuilt": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true")
    prepare(parser.parse_args().rebuild)
