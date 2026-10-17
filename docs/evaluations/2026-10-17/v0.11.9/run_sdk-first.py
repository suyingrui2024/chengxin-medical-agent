import json
import sys
import unittest
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root/'src'),str(root/'tests')]
names=json.loads((root/'.runtime/long-consultation-20261017/skipped-checks.json').read_text())
suite=unittest.defaultTestLoader.loadTestsFromNames(names+['test_glucose_sdk.GlucoseSdkTests'])
assert suite.countTestCases()==51
result=unittest.TextTestRunner(verbosity=2,buffer=True).run(suite)
assert result.testsRun==51 and not result.skipped
raise SystemExit(0 if result.wasSuccessful() else 1)
