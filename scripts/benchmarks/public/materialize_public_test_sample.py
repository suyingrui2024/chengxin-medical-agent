"""Materialize unchanged upstream Python plus frozen, case-local dependencies."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import io
import tarfile

from scripts.benchmarks.public.prepare_public_execution import extract_wheel, safe_parts
from scripts.benchmarks.public.public_execution_cases import sha256
from scripts.benchmarks.public.public_test_sample import ROOT, FIXTURES, inventory

CACHE = ROOT / ".runtime/public-test-sample"
COMMON = [
    "pytest",
    "pytest-mock",
    "pluggy",
    "iniconfig",
    "exceptiongroup",
    "tomli",
    "packaging",
    "pyparsing",
]
SPHINX = [
    "Jinja2",
    "MarkupSafe",
    "docutils",
    "Pygments",
    "snowballstemmer",
    "Babel",
    "pytz",
    "alabaster",
    "imagesize",
    "sphinxcontrib-applehelp",
    "sphinxcontrib-devhelp",
    "sphinxcontrib-htmlhelp",
    "sphinxcontrib-jsmath",
    "sphinxcontrib-qthelp",
    "sphinxcontrib-serializinghtml",
    "html5lib",
    "webencodings",
    "six",
    "setuptools",
    "requests",
    "urllib3",
    "charset-normalizer",
    "idna",
    "certifi",
    "typing_extensions",
]
FLASK = [
    "Jinja2@3.1.2",
    "MarkupSafe",
    "Werkzeug",
    "itsdangerous",
    "click",
    "blinker",
    "python-dotenv",
    "importlib-metadata",
    "zipp",
]
MATPLOTLIB = [
    "contourpy",
    "cycler",
    "fonttools",
    "kiwisolver",
    "Pillow",
    "python-dateutil",
    "six",
    "pandas",
    "pytz",
]

NATIVE_HOOK = '''"""Pinned compiled extensions only; all matplotlib Python stays upstream."""
import importlib.abc,importlib.machinery,sys
from pathlib import Path
native=Path(__file__).parent/'_native'/'matplotlib'
import types
namespace=sys.modules.setdefault('mpl_toolkits',types.ModuleType('mpl_toolkits'))
namespace.__path__=[str(Path(__file__).parent.parent/'lib'/'mpl_toolkits')]
namespace.__package__='mpl_toolkits'
namespace.__spec__=importlib.machinery.ModuleSpec('mpl_toolkits',loader=None,is_package=True)
namespace.__spec__.submodule_search_locations=namespace.__path__
class FrozenNativeFinder(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  parts=fullname.split('.')
  if parts[0]!='matplotlib' or len(parts)<2:return None
  parent=native.joinpath(*parts[1:-1])
  if not any((parent/(parts[-1]+suffix)).is_file() for suffix in importlib.machinery.EXTENSION_SUFFIXES):return None
  return importlib.machinery.PathFinder.find_spec(fullname,[str(parent)])
sys.meta_path.insert(0,FrozenNativeFinder())
'''

RUNNER = '''"""Frozen TEST-sample runner. No reference patch or hidden test is present."""
import os,sys
from pathlib import Path
root=Path(__file__).resolve().parent
historical=root/'.benchmark_dependencies/_python39/python/bin/python3.9'
if historical.is_file() and sys.version_info[:2]!=(3,9):os.execv(str(historical),[str(historical),str(Path(__file__).resolve()),*sys.argv[1:]])
paths=[str(root/'.benchmark_dependencies'),str(root/'src'),str(root/'lib'),str(root)]
sys.path[:0]=paths
os.environ['PYTHONPATH']=os.pathsep.join(paths)
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
os.environ['MPLBACKEND']='agg'
os.environ['OMP_NUM_THREADS']='1'
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['MKL_NUM_THREADS']='1'
if (root/'.benchmark_dependencies/_benchmark_native.py').is_file():import _benchmark_native
import pytest
raise SystemExit(pytest.main(['--rootdir',str(root),'-p','pytest_mock','-p','no:cacheprovider',*sys.argv[1:]]))
'''


def native_extensions(content, target):
    """Extract .so and its bundled shared libraries, never the wheel's Python implementation."""
    import io

    copied = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for entry in archive.infolist():
            parts = safe_parts(entry.filename)
            if entry.is_dir() or not parts:
                continue
            if not (
                parts[0] == "matplotlib.libs"
                or parts[0] == "matplotlib"
                and entry.filename.endswith(".so")
            ):
                continue
            total += entry.file_size
            if total > 100000000:
                raise ValueError("Compiled companion exceeds bound")
            if entry.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("Native archive links not accepted")
            dest = target / "_native" / Path(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read(entry))
            copied.append(str(dest.relative_to(target)))
    if not copied:
        raise ValueError("No compiled extensions in companion wheel")
    (target / "_benchmark_native.py").write_text(NATIVE_HOOK)
    (target / "sitecustomize.py").write_text("import _benchmark_native\n")
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--input-plan", type=Path, default=CACHE / "inputs-v3.json")
    parser.add_argument("--environment-revision", choices=["v4", "v5"], default="v5")
    args = parser.parse_args()
    inputs = json.loads(args.input_plan.read_text())
    selection = json.loads((FIXTURES / "selection.json").read_text())
    revision = args.input_plan.stem.removeprefix("inputs-")
    if revision not in {"v3"}:
        raise ValueError("This materializer revision requires the v3 dependency plan")
    revision = args.environment_revision
    manifest_path = FIXTURES / f"environments-{revision}.json"
    manifest = (
        json.loads(manifest_path.read_text())
        if manifest_path.exists()
        else {
            "kind": "public-test-environments/v1",
            "selection_sha256": sha256((FIXTURES / "selection.json").read_bytes()),
            "cases": {},
        }
    )
    if manifest["selection_sha256"] != sha256(
        (FIXTURES / "selection.json").read_bytes()
    ):
        raise ValueError("Selected sample changed")
    names = args.case or selection["selected_ids"]
    if not set(names) <= set(selection["selected_ids"]):
        raise ValueError("Unselected case")
    for identity in names:
        instance = json.loads((FIXTURES / identity / "instance.json").read_text())
        repo = instance["repo"]
        if repo == "sphinx-doc/sphinx":
            dependencies = COMMON + SPHINX
        elif repo == "pallets/flask":
            dependencies = COMMON + FLASK
        elif repo == "sympy/sympy":
            dependencies = COMMON + ["mpmath"]
        elif repo == "matplotlib/matplotlib":
            dependencies = COMMON + MATPLOTLIB
        else:
            raise ValueError("No preregistered repository environment")
        directory = CACHE / f"prepared-{revision}" / identity
        if directory.exists() and not args.rebuild:
            if inventory(directory) != manifest["cases"][identity]["source_sha256"]:
                raise ValueError("Prepared cache changed")
            continue
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=identity + "-", dir=directory.parent
        ) as raw:
            target = Path(raw) / "source"
            upstream = ROOT / inputs["cases"][identity]["source"]
            upstream_inventory = inventory(upstream)
            shutil.copytree(upstream, target)
            protected = target / ".benchmark_dependencies"
            protected.mkdir()
            for key in dependencies:
                record = inputs["wheels"][key]
                data = (CACHE / "wheels" / record["filename"]).read_bytes()
                if sha256(data) != record["sha256"]:
                    raise ValueError("Dependency wheel changed")
                extract_wheel(data, protected, 150000000)
            native = []
            generated = []
            native_build = None
            if repo == "matplotlib/matplotlib":
                record = inputs["wheels"]["matplotlib"]
                data = (CACHE / "wheels" / record["filename"]).read_bytes()
                if sha256(data) != record["sha256"]:
                    raise ValueError("Native wheel changed")
                native = native_extensions(data, protected)
                if revision == "v5" and identity == "matplotlib__matplotlib-26011":
                    build = CACHE / "native-build-v2" / identity
                    native_build = json.loads((build / "provenance.json").read_text())
                    build_record_path = build / "workspace/build-record.json"
                    build_record = json.loads(build_record_path.read_text())
                    extension = build / "workspace" / build_record["output"]
                    if (
                        native_build["base_source_sha256"] != upstream_inventory
                        or native_build["build_record_sha256"]
                        != sha256(build_record_path.read_bytes())
                        or native_build["output_sha256"]
                        != sha256(extension.read_bytes())
                        or build_record["returncode"] != 0
                    ):
                        raise ValueError("Exact base-source native build drift")
                    destination = protected / "_native/matplotlib" / extension.name
                    if not destination.is_file():
                        raise ValueError("Unexpected compiled companion destination")
                    shutil.copyfile(extension, destination)
                generated_path = target / "lib/matplotlib/_version.py"
                if generated_path.exists():
                    raise ValueError(
                        "Refuse to replace upstream version implementation"
                    )
                with zipfile.ZipFile(io.BytesIO(data)) as wheel:
                    generated_path.write_bytes(wheel.read("matplotlib/_version.py"))
                generated.append(str(generated_path.relative_to(target)))
            if repo == "sphinx-doc/sphinx":
                interpreter = json.loads((CACHE / "python39.json").read_text())
                content = (CACHE / "python" / interpreter["name"]).read_bytes()
                if sha256(content) != interpreter["sha256"]:
                    raise ValueError("Historical interpreter hash changed")
                size = 0
                with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                    for entry in archive:
                        parts = safe_parts(entry.name)
                        if (
                            not entry.isfile()
                            or not parts
                            or entry.name.endswith(".pyc")
                        ):
                            continue
                        size += entry.size
                        if size > 400000000:
                            raise ValueError("Historical interpreter expansion bound")
                        file = protected / "_python39" / Path(*parts)
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_bytes(archive.extractfile(entry).read())
                        file.chmod(0o755 if entry.mode & 0o111 else 0o644)
            if repo == "pallets/flask":
                import ast

                parsed = ast.parse((target / "src/flask/__init__.py").read_text())
                version = next(
                    node.value.value
                    for node in parsed.body
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "__version__"
                        for t in node.targets
                    )
                    and isinstance(node.value, ast.Constant)
                )
                info = protected / f"Flask-{version}.dist-info"
                info.mkdir()
                (info / "METADATA").write_text(
                    f"Metadata-Version: 2.1\nName: Flask\nVersion: {version}\n"
                )
                (info / "top_level.txt").write_text("flask\n")
            (target / "benchmark_run_tests.py").write_text(RUNNER)
            fingerprint = inventory(target)
            if any(
                fingerprint.get(name) != digest
                for name, digest in upstream_inventory.items()
            ):
                raise ValueError("Dependency materialization changed an upstream file")
            if directory.exists():
                shutil.rmtree(directory)
            target.replace(directory)
        manifest["cases"][identity] = {
            "repo": repo,
            "prepared_source": str(directory.relative_to(ROOT)),
            "source_sha256": fingerprint,
            "dependency_keys": dependencies,
            "native_companion": inputs["wheels"]["matplotlib"] if native else None,
            "native_paths": native,
            "exact_source_native_build": native_build,
            "generated_build_files": generated,
            "historical_interpreter": interpreter
            if repo == "sphinx-doc/sphinx"
            else None,
            "upstream_python_replaced_by_wheel": False,
            "unchanged_upstream_files": len(upstream_inventory),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "case": identity,
                    "prepared_files": len(fingerprint),
                    "native_extensions_and_libraries": len(native),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
