"""The adaptive-engine runtime the patient-calculation scenarios share.

Four suites (records, consultation, kernel, extensions) used to reach this
setup by instantiating ``PatientCalculationTests`` and calling ``setUp`` by
hand -- borrowing a test object as a builder.  The setup now lives here, and
both the records tests and every cross-domain caller call the same function.
"""
from medagent.clinical.knowledge import ContentRepository
from medagent.clinical.reasoner import OrderedQuestionRanker
from medagent.clinical.triage import TriageService
from medagent.consultation.agent import AdaptiveConsultationEngine
from medagent.core.events import EventStore
from medagent.extensions.builtin import load_registry
from medagent.guards.self_checks import SelfCheckService

from tests.support.scripted import ScriptBackend


def calculation_runtime():
    """An adaptive session with the capability registry loaded.

    Returns ``(engine, backend, state, opening)``.  The registry matters:
    ``calculate.units`` and the self-check tasks only exist through it, so a
    runtime built without it answers different questions.
    """
    backend = ScriptBackend()
    registry = load_registry()
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(registry),
        triage=TriageService(),
        self_checks=SelfCheckService(registry),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    state, opening = engine.create_session()
    return engine, backend, state, opening
