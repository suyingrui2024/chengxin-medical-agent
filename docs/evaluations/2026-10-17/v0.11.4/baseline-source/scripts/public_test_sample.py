"""Independent public TEST preflight: retain every selected case and required test ID."""

import json
from pathlib import Path
import subprocess
import hashlib
import stat

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "benchmarks/swe-lite-test-sample"
MARKER = "CHENGXIN_TEST_SAMPLE_RESULT="


def inventory(directory):
    result = {}
    total = 0
    for path in sorted(Path(directory).rglob("*")):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Sample source contains links/special files")
        relative = path.relative_to(directory)
        if any(
            part in {"__pycache__", ".pytest_cache", ".git"} for part in relative.parts
        ) or path.suffix in {".pyc", ".pyo"}:
            continue
        total += info.st_size
        if total > 400000000 or len(result) >= 40000:
            raise ValueError("Public TEST sample inventory bound exceeded")
        result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def compliance(directory, original):
    from public_execution_cases import protected
    from execution_task_contract import CONTRACT, regression_file

    try:
        current = inventory(directory)
    except (OSError, ValueError):
        return {"source_readable": False}
    added = set(current) - set(original)
    return {
        "source_readable": True,
        "upstream_tests_and_config_preserved": all(
            current.get(name) == digest
            for name, digest in original.items()
            if protected(name)
        ),
        "no_new_protected_files": not any(protected(name) for name in added),
        "no_new_test_control_files": not any(
            Path(name).name in CONTRACT["forbidden_new_basenames"] for name in added
        ),
        "new_tests_follow_contract": not any(
            (
                Path(name).name.startswith("test_")
                or Path(name).name.endswith("_test.py")
                or Path(name).name == "test.py"
            )
            and name.endswith(".py")
            and not regression_file(name)
            for name in added
        ),
        "python_source_changed": any(
            name.endswith(".py")
            and not protected(name)
            and original.get(name) != digest
            for name, digest in current.items()
        ),
    }


def run_code(directory, state, code, timeout_ms=300000):
    state.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["node", str(ROOT / "scripts/run_public_test_checks.mjs")],
        input=json.dumps(
            {
                "workspace": str(directory.resolve()),
                "state": str(state.resolve()),
                "code": code,
                "timeout_ms": timeout_ms,
                "interpreter": "python39"
                if (
                    directory / ".benchmark_dependencies/_python39/python/bin/python3.9"
                ).is_file()
                else "python3",
            }
        ),
        text=True,
        capture_output=True,
        timeout=timeout_ms / 1000 + 15,
    )
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {
            "passed": False,
            "setup_error": "oracle_transport_output_missing",
            "exit_code": result.returncode,
            "stderr": result.stderr[-4000:],
        }


def code_for(instance, phase, test_id_map=None):
    config = {
        "test_patch": instance["test_patch"],
        "reference_patch": instance["patch"] if phase == "reference" else "",
        "fail_to_pass": json.loads(instance["FAIL_TO_PASS"]),
        "pass_to_pass": json.loads(instance["PASS_TO_PASS"]),
        "phase": phase,
        "test_id_map": test_id_map,
    }
    return r"""import json,os,sys,subprocess,re
from pathlib import Path
os.chdir('/workspace')
configuration=json.loads(CONFIGURATION)
for patch in [configuration['reference_patch'],configuration['test_patch']]:
 if patch:
  result=subprocess.run(['/usr/bin/git','apply','--whitespace=nowarn','-'],input=patch,text=True,capture_output=True,timeout=10)
  if result.returncode:
   print('CHENGXIN_TEST_SAMPLE_RESULT='+json.dumps({'setup_error':'patch_apply_failed','stderr':result.stderr[:2000]}));sys.exit(0)
sys.path[:0]=['/workspace/.benchmark_dependencies','/workspace/src','/workspace/lib','/workspace']
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
os.environ['MPLBACKEND']='agg'
os.environ['PYTHONPATH']=os.pathsep.join(sys.path[:4])
if Path('/workspace/.benchmark_dependencies/_benchmark_native.py').is_file():import _benchmark_native
import pytest
class Collector:
 def __init__(self):self.items=[];self.outcomes={}
 def pytest_collection_modifyitems(self,session,config,items):self.items=[item.nodeid for item in items]
 def pytest_runtest_logreport(self,report):
  if report.when=='call' or report.failed or report.skipped:self.outcomes[report.nodeid]=report.outcome
collector=Collector()
common=['-q','--rootdir','/workspace','--tb=short','-ra','-p','pytest_mock','-p','no:cacheprovider']
if configuration['phase']=='collect':
 files=list(dict.fromkeys(re.findall(r'^[+][+][+] b/(.+[.]py)$',configuration['test_patch'],re.M)))
 for name in configuration['fail_to_pass']+configuration['pass_to_pass']:
  path=name.split('::')[0]
  if path.endswith('.py') and path not in files:files.append(path)
 if not files:raise RuntimeError('No test patch Python files')
 exitcode=int(pytest.main([*common,'--collect-only',*files],plugins=[collector]))
 print('CHENGXIN_TEST_SAMPLE_RESULT='+json.dumps({'pytest_exit_code':exitcode,'collected':collector.items}))
else:
 mapping=configuration['test_id_map']
 targets=list(dict.fromkeys(node for name in configuration['fail_to_pass']+configuration['pass_to_pass'] for node in mapping[name]))
 exitcode=int(pytest.main([*common,*targets],plugins=[collector]))
 print('CHENGXIN_TEST_SAMPLE_RESULT='+json.dumps({'pytest_exit_code':exitcode,'outcomes':collector.outcomes,'test_id_map':mapping,'fail_to_pass':configuration['fail_to_pass'],'pass_to_pass':configuration['pass_to_pass']}))
""".replace(
        "CONFIGURATION", repr(json.dumps(config))
    )


def parse(raw):
    try:
        result = json.loads(raw.get("stdout", "").rsplit(MARKER, 1)[1].strip())
    except (IndexError, ValueError):
        return {"passed": False, "setup_error": "oracle_result_missing", "runtime": raw}
    result["runtime_completed"] = raw.get("passed", False)
    result["stderr"] = raw.get("stderr", "")[-5000:]
    result["test_output_tail"] = raw.get("stdout", "").rsplit(MARKER, 1)[0][-12000:]
    return result


def resolve_ids(instance, collected):
    if not isinstance(collected, list) or not all(
        isinstance(node, str) for node in collected
    ):
        raise ValueError("Invalid collected node IDs")
    mapping = {}
    for name in dict.fromkeys(
        json.loads(instance["FAIL_TO_PASS"]) + json.loads(instance["PASS_TO_PASS"])
    ):
        matches = [node for node in collected if node == name]
        if not matches:
            if "::" in name:
                matches = [
                    node
                    for node in collected
                    if node.startswith(name if "[" in name else name + "[")
                ]
            elif name.endswith(".py"):
                matches = [node for node in collected if node.startswith(name + "::")]
            else:
                matches = [
                    node
                    for node in collected
                    if node.split("::")[-1] == name
                    or node.split("::")[-1].startswith(name + "[")
                ]
        if not matches:
            raise ValueError("Required test ID cannot be resolved: " + name)
        mapping[name] = sorted(set(matches))
    failed = {
        node for name in json.loads(instance["FAIL_TO_PASS"]) for node in mapping[name]
    }
    passed = {
        node for name in json.loads(instance["PASS_TO_PASS"]) for node in mapping[name]
    }
    if failed & passed:
        raise ValueError("Resolved F/P overlap")
    return mapping


def classify(result, reference=False):
    if result.get("setup_error") or not result.get("runtime_completed"):
        return {**result, "passed": False}
    mapping = result["test_id_map"]
    outcomes = result["outcomes"]

    def all_have(names, outcome):
        return all(
            outcomes.get(node) == outcome for name in names for node in mapping[name]
        )

    result["passed"] = result["pytest_exit_code"] == 0 and all_have(
        result["fail_to_pass"] + result["pass_to_pass"], "passed"
    )
    result["original_bug_reproduced"] = (
        bool(result["fail_to_pass"])
        and all_have(result["fail_to_pass"], "failed")
        and all_have(result["pass_to_pass"], "passed")
    )
    return result
