"""The measured-observation scenario: an offered temperature task, one reading.

``measured_case`` builds a session that has already walked the opening facts and
recorded one axillary temperature; ``repeat`` asks for the second reading.  They
moved here from ``tests/dialogue/test_repeated_observations.py``, which was
being imported as a fixture by three other domains.
"""
from medagent.consultation import UserInput
from medagent.consultation.agent import AdaptiveConsultationEngine
from medagent.core.events import EventStore
from medagent.clinical.knowledge import ContentRepository
from medagent.clinical.reasoner import OrderedQuestionRanker
from medagent.guards.self_checks import SelfCheckService
from medagent.clinical.triage import TriageService
from tests.support.scripted import ScriptBackend, cmd, fact


OPENING = "我自己，男，32岁，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。"


DETAILS = (
    "总体变化不大；不适程度3分；没有其他不适；没有气促，能完整说话；干咳，没有咳血；能正常喝水吃饭，排尿正常；自测体温38.25度；没有用药；过敏不知道；没有相关既往病史；想整理信息去就医。"
)


ROWS = [
    ("trajectory", "stable", "变化不大", "present"),
    ("severity", 3, "不适程度3分", "present"),
    ("associated_symptoms", None, "没有其他不适", "absent"),
    ("resp_breathing", "没有气促，能完整说话", "没有气促，能完整说话", "present"),
    ("resp_cough_details", "干咳，没有咳血", "干咳，没有咳血", "present"),
    ("resp_intake", "能正常喝水吃饭，排尿正常", "能正常喝水吃饭，排尿正常", "present"),
    ("reported_temperature", "38.25度", "自测体温38.25度", "present"),
    ("medications_taken", None, "没有用药", "absent"),
    ("allergies", None, "过敏不知道", "unknown"),
    ("relevant_history", None, "没有相关既往病史", "absent"),
    ("patient_goal", "整理信息去就医", "想整理信息去就医", "present"),
]


def measured_case(summary=False, caregiver=False):
    backend = ScriptBackend()
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    state, _ = engine.create_session()
    subject, age, text = "self", 32, OPENING
    if caregiver:
        subject, age, text = "other_adult", 67, OPENING.replace("我自己，男，32岁", "给母亲咨询，她67岁")
    backend.commands = [
        cmd(
            "record_facts",
            facts=[
                fact("consult_for", subject, "给母亲咨询" if caregiver else "我自己"),
                fact("sex", "female" if caregiver else "male", "她" if caregiver else "男"),
                fact("age_years", age, f"{age}岁"),
                fact("chief_complaint", "咳嗽发烧", "咳嗽发烧"),
                fact("onset", "两天", "两天"),
                fact("emergency_screen", None, "上述危险信号都没有", "absent"),
            ],
        ),
        cmd("show_self_check", task_id="axillary_temperature"),
    ]
    reply = engine.submit(state.session_id, UserInput(text))
    assert reply.kind == "self_check", reply.kind
    backend.commands = [cmd("ask_question", question_id="trajectory")]
    reply = engine.submit(state.session_id, UserInput("腋温38.25℃"))
    assert state.facts["temperature_c"].value == 38.25
    if summary:
        backend.commands = [
            cmd("record_facts", facts=[fact(k, v, e, a) for k, v, e, a in ROWS]),
            cmd("review_summary"),
        ]
        reply = engine.submit(state.session_id, UserInput(DETAILS))
        assert reply.kind == "summary", reply.kind
    return engine, backend, state, reply


def repeat(engine, state, reply):
    return engine.submit(
        state.session_id,
        UserInput("再测一次腋温", "repeat_self_check:axillary_temperature", reply.action_id),
    )

