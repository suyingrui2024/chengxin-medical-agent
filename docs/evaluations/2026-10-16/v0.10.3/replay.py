"""Offline replay of saved normal proposals; controlled late-proposal race, no SDK/API."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'snapshot-final/src'))
from medagent.agent import AdaptiveConsultationEngine
from medagent.backends import parse_decision
from medagent.consultation import UserInput
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


def engine_for(backend):
    return AdaptiveConsultationEngine(content=ContentRepository(), triage=TriageService(), self_checks=SelfCheckService(), events=EventStore(), ranker=OrderedQuestionRanker(), backend=backend)


class SavedBackend:
    name = 'offline-saved-proposals'
    def __init__(self, proposals, delayed=False):
        self.proposals = deepcopy(proposals)
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.delayed = delayed
    def decide(self, session_id, messages):
        self.calls += 1
        self.entered.set()
        if self.delayed and not self.release.wait(5):
            raise TimeoutError('Offline fixture not released')
        return self.proposals.pop(0)


def facts(value):
    return {key: {field: entry[field] for field in ['value','assertion','source','quality','evidence']} for key, entry in value['facts'].items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--negative-control', action='store_true')
    args = parser.parse_args()
    report = json.loads((HERE/'real-provider/round1.json').read_text())
    saved = report['runs'][0]
    proposals = [parse_decision(event['payload']['content']) for event in saved['events'] if event['event_type']=='model.responded']
    candidate = deepcopy(proposals)
    if args.negative_control:
        values = candidate[0]['arguments']['facts']
        next(value for value in values if value['key']=='age_years')['value'] = 77
    backend = SavedBackend(candidate)
    engine = engine_for(backend)
    state, opening = engine.create_session()
    reply = engine.submit(state.session_id, UserInput('我自己32岁，咳嗽两天，想整理信息。', expected_action_id=opening.action_id))
    same = facts(state.to_dict()) == facts(saved['state']) and reply.kind == saved['replies'][-1]['kind'] and reply.question_id == saved['replies'][-1]['question_id']
    if args.negative_control:
        assert not same, 'Corrupted saved proposal unexpectedly matched'
        print(json.dumps({'negative_control_rejected': True, 'external_requests': 0}))
        return
    assert same and backend.calls == len(proposals)
    cases=[]
    for name, text, choice in [('raw-priority','现在严重呼吸困难',None),('requested-help','需要先显示求助信息','request_emergency_help')]:
        backend=SavedBackend(proposals, delayed=True)
        engine=engine_for(backend)
        state, opening=engine.create_session()
        result=[]
        thread=threading.Thread(target=lambda: result.append(engine.submit(state.session_id,UserInput('我自己32岁，咳嗽两天，想整理信息。'))))
        thread.start()
        try:
            assert backend.entered.wait(2)
            reply=engine.submit(state.session_id,UserInput(text,choice,'obsolete'))
            revision=state.revision
            thread.join(2)
            assert not thread.is_alive() and result[0].action_id==reply.action_id
            assert reply.kind=='emergency' and not state.facts
            backend.release.set()
            deadline=time.monotonic()+2
            while not any(event.event_type=='agent.decision_discarded' for event in engine.events.for_session(state.session_id)):
                assert time.monotonic()<deadline
                time.sleep(0.01)
            assert state.revision==revision and not state.facts and backend.calls==1
            cases.append({'case':name,'passed':True})
        finally:
            backend.release.set()
            thread.join(3)
    print(json.dumps({'saved_normal_proposals_replayed':len(proposals),'normal_state_and_question_match':same,'controlled_priority_replays':cases,'late_proposal_source':'Saved normal-case proposal deliberately delayed; priority-case cancelled provider bodies were not archived.','external_requests':0},ensure_ascii=False))

if __name__=='__main__':
    main()
