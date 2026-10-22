"""Bounded synthetic real-provider check of reported relatives and separate cases."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import secrets
import threading

from medagent.agent import AdaptiveConsultationEngine
from medagent.backends import DshBackend
from medagent.consultation import UserInput
from medagent.env_config import for_backend, read_env
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from scripts.evaluation.medical.test_live_desktop import EvaluationGateway


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh output required")
    root = Path(__file__).resolve().parents[3]
    frozen = json.loads(args.source_manifest.read_text())

    def unchanged():
        return all(
            (root / n).is_file()
            and hashlib.sha256((root / n).read_bytes()).hexdigest() == v
            for n, v in frozen.items()
        )

    assert unchanged()
    spent = 0
    for p in args.output.parent.glob("*.json"):
        try:
            old = json.loads(p.read_text())
            if old.get("evaluation") == "consultation-subjects/v1":
                spent += max(0, int(old.get("attempted_calls", 0)))
        except (ValueError, OSError):
            pass
    maximum = min(10, 20 - spent)
    if maximum <= 0:
        raise SystemExit("Cumulative 20-call evaluation limit reached")
    settings = for_backend(read_env(root / ".env"), "dsh")
    token = secrets.token_hex(32)
    gateway = EvaluationGateway(settings, maximum, token)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_API_KEY": token,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
    }
    events, checks = EventStore(), {}
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=events,
        ranker=OrderedQuestionRanker(),
        backend=DshBackend(events, root, config),
    )
    father, mother = None, None
    failure = None
    try:
        father, _ = engine.create_session()
        reply = engine.submit(father.session_id, UserInput("我父亲68岁，头疼两天；上述危险信号都没有。"))
        checks["real_opening_records_father"] = (
            reply.kind in {"question", "clarification"}
            and father.get("age_years") == 68
            and (father.to_dict()["consultation_subject"] or {}).get("label") == "父亲"
        )
        assert checks["real_opening_records_father"]
        original = deepcopy(father.to_dict()["facts"])
        previous = reply
        count = gateway.metrics()["attempted_calls"]
        changed = engine.submit(
            father.session_id,
            UserInput("改问我母亲，她65岁，肚子痛。", expected_action_id=reply.action_id),
        )
        checks["explicit_switch_keeps_father_and_uses_zero_provider_calls"] = (
            changed.kind == "subject_change"
            and father.to_dict()["facts"] == original
            and gateway.metrics()["attempted_calls"] == count
        )
        assert checks["explicit_switch_keeps_father_and_uses_zero_provider_calls"]
        reply = engine.submit(
            father.session_id,
            UserInput("继续父亲问诊", "resume_current_subject", changed.action_id),
        )
        checks["return_restores_original_question_without_model"] = (
            reply.message == previous.message
            and reply.question_id == previous.question_id
            and gateway.metrics()["attempted_calls"] == count
        )
        assert checks["return_restores_original_question_without_model"]
        background = "补充相关背景：我母亲患有糖尿病，这是我母亲的情况，不是我父亲本人患病。"
        reply = engine.submit(
            father.session_id, UserInput(background, expected_action_id=reply.action_id)
        )
        history = father.facts.get("relevant_history")
        checks["other_relative_is_not_recorded_as_patient_disease"] = (
            reply.kind in {"question", "clarification", "summary"}
            and (
                history is None
                or history.assertion.value == "present"
                and "母亲" in str(history.value)
            )
            and father.get("age_years") == 68
            and father.to_dict()["consultation_subject"]["label"] == "父亲"
        )
        assert checks["other_relative_is_not_recorded_as_patient_disease"]
        before = deepcopy(father.to_dict()["facts"])
        semantic = "接下来为我母亲咨询她本人的不适，这一条不是父亲的相关病史。请先显示切换就诊对象的入口，暂不要记录她的症状。"
        changed = engine.submit(
            father.session_id, UserInput(semantic, expected_action_id=reply.action_id)
        )
        checks["model_can_request_a_separate_subject_card"] = (
            changed.kind == "subject_change"
            and changed.subject_change["requested"]["label"] == "母亲"
            and father.to_dict()["facts"] == before
        )
        assert checks["model_can_request_a_separate_subject_card"]
        mother, _ = engine.create_session()
        checks["new_case_starts_empty"] = (
            not mother.facts and mother.session_id != father.session_id
        )
        reply = engine.submit(mother.session_id, UserInput("我母亲65岁，肚子痛一天；上述危险信号都没有。"))
        checks["new_real_case_belongs_to_mother"] = (
            reply.kind in {"question", "clarification"}
            and mother.get("age_years") == 65
            and mother.to_dict()["consultation_subject"]["label"] == "母亲"
        )
        checks["new_case_does_not_modify_father"] = father.to_dict()["facts"] == before
        checks["no_fact_ids_shared_between_cases"] = not (
            {f.fact_id for f in father.history} & {f.fact_id for f in mother.history}
        )
        assert all(checks.values())
    except Exception as exc:
        failure = type(exc).__name__
    finally:
        gateway.shutdown()
        gateway.server_close()
        stable = unchanged()
        observed = (
            [
                e.payload["tool"]
                for e in events.for_session(father.session_id)
                if e.event_type == "tool.requested"
            ]
            if father
            else []
        )
        report = {
            "evaluation": "consultation-subjects/v1",
            "synthetic_only": True,
            "scope": "Two fresh real-provider synthetic cases with a zero-model literal boundary and return, a background attribution check and a model-controlled boundary. GUI draft handling is verified separately with local model fixtures, not by this script. No clinical validation or verified personal identity.",
            "checks": checks,
            "failure_type": failure,
            "observed_father_tools": observed,
            "change_subject_tool_observed": "change_subject" in observed,
            "father_state": father.to_dict() if father else None,
            "mother_state": mother.to_dict() if mother else None,
            "events": {
                s.session_id: [e.to_dict() for e in events.for_session(s.session_id)]
                for s in [father, mother]
                if s
            },
            "frozen_sources_unchanged": stable,
            **gateway.metrics(),
            "passed": failure is None and all(checks.values()) and stable,
        }
        encoded = (
            json.dumps(report, ensure_ascii=False, indent=2)
            .replace(token, "[evaluation-token-redacted]")
            .replace(settings["MEDAGENT_LLM_API_KEY"], "[provider-key-redacted]")
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n")
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "attempted_calls": report["attempted_calls"],
                    "failure_type": failure,
                    "checks": checks,
                    "change_subject_tool_observed": report[
                        "change_subject_tool_observed"
                    ],
                },
                ensure_ascii=False,
            )
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
