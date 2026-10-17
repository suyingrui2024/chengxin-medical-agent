"""Build the selected historical _path extension from its exact base source, in isolation."""

import json
from pathlib import Path
import shutil

from public_test_sample import ROOT, inventory, run_code
from public_execution_cases import sha256

CACHE = ROOT / ".runtime/public-test-sample"
IDENTITY = "matplotlib__matplotlib-26011"
OUTPUT = CACHE / "native-build-v2" / IDENTITY
SOURCES = [
    "src/py_converters.cpp",
    "src/_path_wrapper.cpp",
    *[
        "extern/agg24-svn/src/" + name + ".cpp"
        for name in [
            "agg_bezier_arc",
            "agg_curves",
            "agg_image_filters",
            "agg_trans_affine",
            "agg_vcgen_contour",
            "agg_vcgen_dash",
            "agg_vcgen_stroke",
            "agg_vpgen_segmentator",
        ]
    ],
]


def main():
    if OUTPUT.exists():
        raise SystemExit("Use the existing build record; refuse an implicit rebuild")
    source = CACHE / "sources" / IDENTITY
    original = inventory(source)
    workspace = OUTPUT / "workspace"
    shutil.copytree(source, workspace)
    if inventory(workspace) != original:
        raise ValueError("Base source copy changed")
    code = r"""import subprocess,sysconfig,json,hashlib
from pathlib import Path
import numpy
sources=json.loads(SOURCES)
out='built/_path'+sysconfig.get_config_var('EXT_SUFFIX')
Path('built').mkdir()
command=['/usr/bin/g++','-shared','-fPIC','-O2','-std=c++11',
 '-DPY_ARRAY_UNIQUE_SYMBOL=MPL_matplotlib__path_ARRAY_API',
 '-DNPY_NO_DEPRECATED_API=NPY_1_7_API_VERSION','-D__STDC_FORMAT_MACROS=1',
 '-I'+numpy.get_include(),'-I'+sysconfig.get_paths()['include'],
 '-Iextern/agg24-svn/include',*sources,'-o',out]
result=subprocess.run(command,capture_output=True,text=True,timeout=240)
record={'command':command,'returncode':result.returncode,
 'numpy_version':numpy.__version__,'numpy_path':numpy.__file__,
 'source_files':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in sources},
 'compiler_sha256':hashlib.sha256(Path('/usr/bin/g++').read_bytes()).hexdigest(),
 'output':out}
if result.returncode==0:record['sha256']=hashlib.sha256(Path(out).read_bytes()).hexdigest()
Path('build-record.json').write_text(json.dumps(record,indent=2)+'\n')
print(result.stdout);print(result.stderr)
raise SystemExit(result.returncode)
""".replace(
        "SOURCES", repr(json.dumps(SOURCES))
    )
    verification = run_code(workspace, OUTPUT / "state", code)
    (OUTPUT / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    if not verification.get("passed"):
        raise SystemExit(
            "Exact-source extension build failed; retained full diagnostics"
        )
    current = inventory(workspace)
    if any(current.get(name) != value for name, value in original.items()):
        raise ValueError("Build changed upstream sources")
    record_path = workspace / "build-record.json"
    record = json.loads(record_path.read_text())
    provenance = {
        "case": IDENTITY,
        "base_source_sha256": original,
        "build_record_sha256": sha256(record_path.read_bytes()),
        "output_sha256": sha256((workspace / record["output"]).read_bytes()),
        "source_unchanged_after_build": True,
        "build_isolation": "same bubblewrap filesystem as the two evaluation arms",
    }
    (OUTPUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"passed": True, "output_sha256": provenance["output_sha256"]}))


if __name__ == "__main__":
    main()
