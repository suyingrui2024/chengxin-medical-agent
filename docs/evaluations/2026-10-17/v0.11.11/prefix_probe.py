"""Construct the static medical prompt without asking any model."""
import argparse
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();assert not a.output.exists()
root=a.project.resolve();sys.path[:0]=[str(root/'src'),str(root)]
from tests import test_patient_calculations as fixture
helper=fixture.PatientCalculationTests();helper.setUp();report={'static_prefix_sha256':helper.engine.prefix_sha256,'external_model_calls':0,'tool_count':len(helper.engine.static_context['tools']),'scope':'Only static prompt bytes, not clinical correctness or all dynamic model inputs.'};a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
