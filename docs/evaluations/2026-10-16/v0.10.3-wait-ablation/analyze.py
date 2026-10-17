"""Descriptive wait-policy metrics; parse structured events, never terminal claims."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def describe(row):
    snapshot = row['snapshots'][0] if row.get('snapshots') else {}
    events={}
    observed={}
    for envelope in snapshot.get('events',[]):
        if envelope.get('type')=='tool.observed':
            observation=envelope['observation']
            observed[observation['observation_id']]=observation
        notification=envelope.get('notification',{})
        if notification.get('method')!='session.event':
            continue
        params=notification['params']; event=params['event']
        if event.get('type') not in {'tool/call','tool/result'}:
            continue
        key=(params['sessionId'],event['type'],event.get('seq'),event.get('time'))
        events[key]=(params['sessionId'],event)
    calls={}; results={}
    for session,event in events.values():
        data=event.get('data',{})
        if event['type']=='tool/call':
            args=data.get('arguments',{})
            if isinstance(args,str):
                try:args=json.loads(args)
                except ValueError:args={'unparsed':args}
            calls[(session,data.get('callId'))]={'name':data.get('name'),'arguments':args,'time':event.get('time')}
        else:
            message=data.get('message',{})
            results[(session,message.get('toolCallId'))]=event
    waits=[]
    for key,call in calls.items():
        if call['name']!='job_output':continue
        result=results.get(key,{})
        duration=(result['time']-call['time']) if isinstance(result.get('time'),(int,float)) and isinstance(call['time'],(int,float)) else None
        observation=next((value for value in observed.values() if value['session_id']==key[0] and value['call_id']==key[1]),None)
        waits.append({'session_id':key[0],'call_id':key[1],'arguments':call['arguments'],'elapsed_ms':duration,'structured_observation':observation})
    verification=row.get('verification',{})
    return {**{key:row[key] for key in ['case','repetition','arm','runtime_completed','source_correct','source_and_contract_passed','end_to_end_completed']},'requests':row['gateway']['attempted_calls'],'execution_ms':row['execution_duration_ms'],'all_provider_http200':all(call['status']==200 for call in row['gateway']['calls']),'reason':snapshot.get('error'),'tool_counts':dict(Counter(value['name'] for value in calls.values())),'waits':waits,'wait_tool_calls':len(waits),'explicit_waits_above_30_requested':sum(wait['arguments'].get('wait') is True and wait['arguments'].get('timeout_ms',0)>30000 for wait in waits),'waits_above_30_observed':sum(wait['elapsed_ms'] is not None and wait['elapsed_ms']>31000 for wait in waits),'verified_outcome_counts':dict(Counter(verification.get('outcomes',{}).values())),'pytest_exit_code':verification.get('pytest_exit_code')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise SystemExit('Use fresh analysis output')
    source=args.report.read_bytes();report=json.loads(source)
    rows=[describe(row) for row in report['runs']]
    arms={}
    for arm in ['wait-30','wait-600']:
        selected=[row for row in rows if row['arm']==arm]
        arms[arm]={'runs':len(selected),'requests':sum(row['requests'] for row in selected),'source_passed':sum(row['source_correct'] is True for row in selected),'source_failed':sum(row['source_correct'] is False for row in selected),'source_unavailable':sum(row['source_correct'] is None for row in selected),'file_and_source_passed':sum(row['source_and_contract_passed'] for row in selected),'runtime_completed':sum(row['runtime_completed'] for row in selected),'end_to_end_completed':sum(row['end_to_end_completed'] for row in selected),'job_output_calls':sum(row['wait_tool_calls'] for row in selected),'explicit_waits_above_30_requested':sum(row['explicit_waits_above_30_requested'] for row in selected),'waits_above_30_observed':sum(row['waits_above_30_observed'] for row in selected)}
    pairs=[]
    for identity,repetition in sorted(set((row['case'],row['repetition']) for row in rows)):
        matching={row['arm']:row for row in rows if row['case']==identity and row['repetition']==repetition}
        if len(matching)!=2:continue
        control,candidate=matching['wait-30'],matching['wait-600']
        pairs.append({'case':identity,'repetition':repetition,'candidate_minus_control_requests':candidate['requests']-control['requests'],'candidate_minus_control_execution_ms':candidate['execution_ms']-control['execution_ms'],'control_source_passed':control['source_correct'],'candidate_source_passed':candidate['source_correct'],'control_joint_passed':control['end_to_end_completed'],'candidate_joint_passed':candidate['end_to_end_completed']})
    result={'scope':'Two known development tasks, two repetitions. Descriptive policy-bundle ablation, no holdout, clinical validity, statistical superiority or overall SDK parity claim. Wait durations measure tool wall time; completion and exit status come only from structured observations and independent tests.','report_sha256':hashlib.sha256(source).hexdigest(),'analysis_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'plan_finished':report['plan_finished'],'frozen_source_unchanged':report['frozen_source_unchanged'],'runs':rows,'arms':arms,'pairs':pairs,'long_wait_elapsed_threshold_ms':31000,'model_requests_for_analysis':0}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'completed_runs':len(rows),'plan_finished':result['plan_finished'],'arms':arms},ensure_ascii=False))

if __name__=='__main__':main()
