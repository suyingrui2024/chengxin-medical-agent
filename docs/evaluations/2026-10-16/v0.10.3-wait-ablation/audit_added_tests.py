"""Uniform post-hoc check of every delivered new regression file; no model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'src'),str(HERE.parent/'v0.10.2')]
from candidate_tree import rebuild
from execution_task_contract import CONTRACT, regression_file
from public_execution_cases import protected
from public_test_sample import run_code, code_for, parse


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise SystemExit('Fresh audit output required')
    raw=args.report.read_bytes();report=json.loads(raw)
    if not all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected for name,expected in report['source_sha256'].items()):raise SystemExit('Runtime or evaluator source drift')
    environments=json.loads((ROOT/'benchmarks/swe-lite-test-sample/environments-v5.json').read_text())['cases']
    output={'kind':'uniform-added-regression-audit/v1','posthoc':True,'reason':'A wrong cosh(0) expected value was observed in repetition 2 wait-600; apply the same check to every run, keeping original scores unchanged.','original_report_sha256':hashlib.sha256(raw).hexdigest(),'audit_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'completed_runs_at_snapshot':len(report['runs']),'plan_finished':report['plan_finished'],'model_requests':0,'runs':[]}
    source_snapshot=args.output.with_suffix('.original-report.json')
    if source_snapshot.exists():raise SystemExit('Snapshot already exists')
    source_snapshot.write_bytes(raw)
    def save():args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    save()
    for row in report['runs']:
        identity=row['case'];environment=environments[identity];baseline=environment['source_sha256']
        item={key:row[key] for key in ['case','arm','repetition','source_correct','source_and_contract_passed','runtime_completed','end_to_end_completed']}
        item.update(new_regression_status='unavailable',new_regression_passed=None)
        try:
            tree=row['candidate_tree']
            added=sorted(name for name in tree['files'] if name not in baseline and regression_file(name))
            item['new_regression_files']=added
            if not added:
                item['new_regression_status']='not_applicable'
                item['added_test_gate_passed']=True
            else:
                folder=args.report.with_suffix('')/f"r{row['repetition']}-{identity}-{row['arm']}"
                with tempfile.TemporaryDirectory(prefix='wait-added-tests-') as raw_directory:
                    temporary=Path(raw_directory)
                    workspace=rebuild(ROOT/environment['prepared_source'],folder/'candidate',tree,baseline,temporary/'workspace',protected,CONTRACT['forbidden_new_basenames'])
                    synthetic={'test_patch':'','patch':'','FAIL_TO_PASS':json.dumps(added),'PASS_TO_PASS':'[]'}
                    # File paths here select delivered tests, not correctness labels.
                    # Inspect every real collected node and the actual pytest exit.
                    mapping={name:[name] for name in added}
                    verification=parse(run_code(workspace,temporary/'state',code_for(synthetic,'candidate',mapping)))
                item['verification']=verification
                available=verification.get('runtime_completed') and not verification.get('setup_error')
                if available:
                    outcomes=verification.get('outcomes',{})
                    item['all_files_collected']=all(any(node.startswith(name+'::') for node in outcomes) for name in added)
                    passed=verification['pytest_exit_code']==0 and bool(outcomes) and item['all_files_collected'] and all(value=='passed' for value in outcomes.values())
                    item['new_regression_passed']=passed
                    item['new_regression_status']='passed' if passed else 'failed'
                item['added_test_gate_passed']=item['new_regression_passed'] is True
        except Exception as error:
            item['error_type']=type(error).__name__
            item['diagnostic']=str(error)[:1000]
            item['added_test_gate_passed']=False
        item['joint_plus_delivered_tests_passed']=item['end_to_end_completed'] and item['added_test_gate_passed']
        output['runs'].append(item)
        save()
        print(json.dumps({key:item[key] for key in ['case','arm','repetition','new_regression_status','joint_plus_delivered_tests_passed']}),flush=True)

if __name__=='__main__':main()
