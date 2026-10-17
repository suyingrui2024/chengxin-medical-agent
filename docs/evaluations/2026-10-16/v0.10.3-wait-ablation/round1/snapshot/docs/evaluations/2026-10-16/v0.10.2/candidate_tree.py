"""Bounded candidate snapshots: regular bytes and link text, never link targets."""

import hashlib
import os
from pathlib import Path
import shutil
import stat


def regular_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 400000000:
            raise ValueError("Not a bounded regular file")
        chunks = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1048576))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        content = b"".join(chunks)
        if len(content) != before.st_size or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("Candidate file changed during snapshot")
        return content
    finally:
        os.close(descriptor)


def scan(directory):
    directory = Path(directory)
    files, links, special = {}, {}, {}
    total = 0
    count = 0
    for raw, directories, names in os.walk(directory, followlinks=False):
        parent = Path(raw)
        for name in sorted([*directories, *names]):
            path = parent / name
            relative = path.relative_to(directory)
            info = path.lstat()
            count += 1
            if count > 80000:
                raise ValueError("Candidate entry bound exceeded")
            if stat.S_ISLNK(info.st_mode):
                links[str(relative)] = os.readlink(path)
                if name in directories:
                    directories.remove(name)
            elif stat.S_ISDIR(info.st_mode):
                if name in {"__pycache__", ".pytest_cache", ".git"}:
                    directories.remove(name)
            elif not stat.S_ISREG(info.st_mode):
                special[str(relative)] = stat.S_IFMT(info.st_mode)
            elif not any(
                part in {"__pycache__", ".pytest_cache", ".git"}
                for part in relative.parts
            ) and path.suffix not in {".pyc", ".pyo"}:
                total += info.st_size
                if total > 400000000 or len(files) >= 40000:
                    raise ValueError("Candidate regular-file bound exceeded")
                files[str(relative)] = hashlib.sha256(regular_bytes(path)).hexdigest()
    return {"files": files, "links": links, "special": special, "regular_bytes": total}


def rebuild(prepared, candidate, tree, baseline, destination, protected, forbidden):
    """Restore protected inputs, copy ordinary bytes, then create links for isolated execution."""
    candidate, destination = Path(candidate), Path(destination)
    if tree["special"]:
        raise ValueError(
            "Special candidate files require an explicit unsupported assessment"
        )
    files, links = tree["files"], tree["links"]
    for name in [*files, *links]:
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or any(str(parent) in links for parent in path.parents)
        ):
            raise ValueError("Unsafe or overlapping candidate path")

    def frozen(name):
        return protected(name) or Path(name).name in forbidden

    def remove_directory(path, name):
        if any(old.startswith(name + "/") and frozen(old) for old in baseline):
            raise ValueError(
                "Cannot reconstruct an implementation alias above protected inputs"
            )
        shutil.rmtree(path)

    shutil.copytree(prepared, destination)
    for name, digest in baseline.items():
        if not frozen(name) and files.get(name) != digest:
            (destination / name).unlink(missing_ok=True)
    for name, digest in files.items():
        if frozen(name) or baseline.get(name) == digest:
            continue
        data = regular_bytes(candidate / name)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Candidate changed since snapshot")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            remove_directory(target, name)
        target.write_bytes(data)
    # No host-side traversal, inventory, reads or writes through the links after this point.
    # Absolute /workspace targets resolve only when the fixed bwrap oracle is launched.
    for name, target_text in sorted(links.items()):
        if frozen(name):
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            remove_directory(target, name)
        else:
            target.unlink(missing_ok=True)
        os.symlink(target_text, target)
    return destination
