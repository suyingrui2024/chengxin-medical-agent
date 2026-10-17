"""Same synthetic probe against an explicit copied project; no external model."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode=True
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--project",type=Path,required=True)
parser.add_argument("--output",type=Path,required=True)
args=parser.parse_args()
project=args.project.resolve()
assert not args.output.exists()
sys.path[:0]=[str(project/'src'),str(project)]
from medagent import __version__
from medagent.consultation import UserInput
from medagent.extensions.builtin import observation
from tests.test_repeated_observations import measured_case,repeat
from tests.test_agent import cmd,fact

engine,backend,state,prior=measured_case(summary=True)
offered=repeat(engine,state,prior)
medication="昨晚用过药物甲，用量不清楚"
backend.commands=[cmd("record_facts",facts=[fact("medications_taken",medication,medication,correction=True)]),cmd("review_summary")]
before=len(backend.calls)
reply=engine.submit(state.session_id,UserInput("腋温37.6℃；更正："+medication+"。",expected_action_id=offered.action_id))
cases=[{"case":"measurement_plus_medication_update","reply_kind":reply.kind,"temperature":state.get("temperature_c"),"medication_assertion":state.facts["medications_taken"].assertion.value,"medication_value":state.get("medications_taken"),"scripted_decisions_after_measurement":len(backend.calls)-before}]
for text in ["腋温38.6℃，读数可能不准","腋温38.6℃，这个读数我不确定","腋温37.6℃，昨晚用药物甲2片"]:
    try:
        result=observation(text,None,"temperature")
        case={"assertion":result.assertion.value,"quality":result.quality.value,"value":result.value}
    except Exception as exc: case={"error_type":type(exc).__name__}
    cases.append({"case":"full_measurement_report","text":text,**case})
report={"synthetic_only":True,"external_model_calls":0,"version":__version__,"scope":"Same actual controller/registered-parser probe with deterministic decision responses; not a model error-rate or clinical comparison.","cases":cases}
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"version":__version__,"medication_assertion":cases[0]["medication_assertion"],"qualifier_assertions":[c.get("assertion") for c in cases[1:3]],"dose_report_result":cases[3]}))
