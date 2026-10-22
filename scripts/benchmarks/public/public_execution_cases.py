"""Frozen public development tasks; reference patches never enter agent mounts."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import hashlib
import json
from pathlib import Path
import re
import shutil
import stat

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "benchmarks/swe-lite-dev"
MARKER = "CHENGXIN_PUBLIC_TEST_RESULT="
PUBLIC_RUNNER = '''"""Local test helper; no hidden tests or reference implementation."""
import os,sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path[:0]=[str(root/".benchmark_dependencies"),str(root/"src"),str(root)]
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]="1"
import pytest
raise SystemExit(pytest.main(["--rootdir",str(root),"-c","/dev/null","-p","pytest_mock","-p","no:cacheprovider",*sys.argv[1:]]))
'''


def compatibility_bootstrap(configuration=None):
    configuration = configuration or {}
    if set(configuration) - {
        "collections_mapping_alias",
        "subprocess_pythonpath",
    } or any(type(value) is not bool for value in configuration.values()):
        raise ValueError("Unsupported frozen environment compatibility")
    lines = []
    if configuration.get("collections_mapping_alias"):
        lines.extend(
            [
                "import collections,collections.abc",
                "if not hasattr(collections,'Mapping'):collections.Mapping=collections.abc.Mapping",
            ]
        )
    if configuration.get("subprocess_pythonpath"):
        lines.append("os.environ['PYTHONPATH']=os.pathsep.join(sys.path[:3])")
    return "\n".join(lines) + ("\n" if lines else "")


def public_runner(configuration=None):
    return PUBLIC_RUNNER.replace(
        "import pytest\n", compatibility_bootstrap(configuration) + "import pytest\n", 1
    )


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def inventory(directory):
    result, total = {}, 0
    for path in sorted(Path(directory).rglob("*")):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Public benchmark only accepts regular files")
        if any(
            part in {"__pycache__", ".pytest_cache", ".git"}
            for part in path.relative_to(directory).parts
        ) or path.suffix in {".pyc", ".pyo"}:
            continue
        total += info.st_size
        if total > 150_000_000 or len(result) >= 20000:
            raise ValueError("Public benchmark source bound exceeded")
        result[str(path.relative_to(directory))] = sha256(path.read_bytes())
    return result


PROTECTED_DIRECTORIES = frozenset({"tests", "test", ".benchmark_dependencies"})
PROTECTED_BASENAMES = frozenset(
    {
        "conftest.py",
        "pyproject.toml",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "setup.py",
        "benchmark_run_tests.py",
        "LICENSE",
        "LICENSE.txt",
        "LICENSE.rst",
    }
)
TEST_CONTROL_FILES = frozenset(
    {
        "conftest.py",
        "sitecustomize.py",
        "usercustomize.py",
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
    }
)


def protected(name):
    parts = Path(name).parts
    return (
        any(part in PROTECTED_DIRECTORIES for part in parts)
        or Path(name).name in PROTECTED_BASENAMES
    )


def load_cases(fixtures=FIXTURES):
    fixtures = Path(fixtures)
    manifest = json.loads((fixtures / "manifest.json").read_text())
    result = {}
    for item in manifest["cases"]:
        identity = item["instance_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identity):
            raise ValueError("Invalid public instance ID")
        base = fixtures / identity
        instance = json.loads((base / "instance.json").read_text())
        for name, expected in item["sha256"].items():
            if (
                Path(name).name != name
                or sha256((base / name).read_bytes()) != expected
            ):
                raise ValueError("Frozen public task changed")
        result[identity] = {"metadata": instance, "provenance": item, "directory": base}
    return manifest, result


def seed(directory, case):
    source = (ROOT / case["provenance"]["source_cache"]).resolve()
    source.relative_to(ROOT / ".runtime/public-benchmark-research/sources")
    if inventory(source) != case["provenance"]["source_sha256"]:
        raise ValueError("Frozen source/dependency cache differs")
    shutil.copytree(source, directory, dirs_exist_ok=True)
    return inventory(directory)


def verification_code(case, reference=False):
    metadata = case["metadata"]
    mapping = case["provenance"].get("test_id_map", {})

    def resolved(names):
        result = []
        for name in names:
            target = mapping.get(name, name)
            targets = target if isinstance(target, list) else [target]
            if not targets or any(
                not isinstance(item, str) or not item.startswith(name)
                for item in targets
            ):
                raise ValueError("Invalid public test ID resolution")
            result.extend(targets)
        return list(dict.fromkeys(result))

    configuration = {
        "patch": metadata["patch"] if reference else "",
        "test_patch": metadata["test_patch"],
        "fail_to_pass": resolved(json.loads(metadata["FAIL_TO_PASS"])),
        "pass_to_pass": resolved(json.loads(metadata["PASS_TO_PASS"])),
        "test_id_map": mapping,
    }
    if set(configuration["fail_to_pass"]) & set(configuration["pass_to_pass"]):
        raise ValueError("Conflicting public test classifications")
    code = """import json,os,sys,subprocess
from pathlib import Path
os.chdir('/workspace')
sys.path[:0]=['/workspace/.benchmark_dependencies','/workspace/src','/workspace']
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
configuration=json.loads(CONFIGURATION)
for patch in [configuration['patch'],configuration['test_patch']]:
 if patch:
  result=subprocess.run(['/usr/bin/git','apply','--whitespace=nowarn','-'],input=patch,text=True,capture_output=True,timeout=5)
  if result.returncode:
   print('CHENGXIN_PUBLIC_TEST_RESULT='+json.dumps({'setup_error':'patch_apply_failed','diagnostic':result.stderr[:1000]}));sys.exit(0)
import pytest
assert str(Path(pytest.__file__).resolve()).startswith('/usr/'), 'pytest must be the fixed system package'
class Collector:
 def __init__(self):self.outcomes={}
 def pytest_runtest_logreport(self,report):
  if report.when=='call' or report.failed or report.skipped:self.outcomes[report.nodeid]=report.outcome
collector=Collector()
targets=list(dict.fromkeys(configuration['fail_to_pass']+configuration['pass_to_pass']))
exitcode=int(pytest.main(['-q','--rootdir','/workspace','-c','/dev/null','--tb=short','-ra','-p','pytest_mock','-p','no:cacheprovider',*targets],plugins=[collector]))
print('CHENGXIN_PUBLIC_TEST_RESULT='+json.dumps({'pytest_exit_code':exitcode,'outcomes':collector.outcomes,'expected':targets,'fail_to_pass':configuration['fail_to_pass'],'pass_to_pass':configuration['pass_to_pass'],'test_id_map':configuration['test_id_map']}))
""".replace(
        "CONFIGURATION", repr(json.dumps(configuration))
    )
    return code.replace(
        "import pytest\n",
        compatibility_bootstrap(case["provenance"].get("compatibility"))
        + "import pytest\n",
        1,
    )


def verify(directory, state, case, reference=False):
    from scripts.benchmarks.benchmark_execution import check_artifacts

    result = check_artifacts(directory, state, verification_code(case, reference))
    try:
        data = json.loads(result.get("stdout", "").rsplit(MARKER, 1)[1].strip())
    except (ValueError, IndexError):
        return {
            "passed": False,
            "setup_error": "verifier_output_missing",
            "runtime": result,
        }
    data["runtime_completed"] = result.get("passed", False)
    if "setup_error" not in data:
        actual = data["outcomes"]
        data["passed"] = (
            data["runtime_completed"]
            and data["pytest_exit_code"] == 0
            and all(actual.get(name) == "passed" for name in data["expected"])
        )
        data["original_bug_reproduced"] = (
            bool(data["fail_to_pass"])
            and all(actual.get(name) == "failed" for name in data["fail_to_pass"])
            and all(actual.get(name) == "passed" for name in data["pass_to_pass"])
        )
    else:
        data["passed"] = False
    data["stderr"] = result.get("stderr", "")[-4000:]
    data["test_output"] = result.get("stdout", "").rsplit(MARKER, 1)[0][-8000:]
    data["stdout_tail"] = result.get("stdout", "")[-6000:]
    return data


def frozen_source_check(directory, original):
    try:
        current = inventory(directory)
    except (ValueError, OSError):
        return {"source_readable": False}
    return {
        "source_readable": True,
        "upstream_tests_and_config_preserved": all(
            current.get(name) == value
            for name, value in original.items()
            if protected(name)
        ),
        "no_new_test_control_files": all(
            name in original
            for name in current
            if Path(name).name in TEST_CONTROL_FILES
        ),
        "python_source_changed": any(
            not protected(name) and name.endswith(".py") and original.get(name) != value
            for name, value in current.items()
        ),
    }
