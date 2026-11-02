"""``_execute`` is a table lookup now, so the table has to stay complete.

It used to be one 900-line if-chain: a tool could only go missing if somebody
deleted its branch, and the failure showed up as a crash on the next call.
With a mapping there is no such accident, but also no compiler help — these
checks are what keeps ``_TOOLS`` and the validated tool set in step.
"""

from medagent.consultation.agent.execute import AdaptiveConsultationEngineExecute
from medagent.infra.agent_tools import ARGUMENTS

TOOLS = AdaptiveConsultationEngineExecute._TOOLS


def test_every_validated_tool_is_dispatchable():
    """A tool the model is allowed to propose must have somewhere to go."""
    assert set(ARGUMENTS) - set(TOOLS) == set()


def test_the_table_offers_nothing_the_validator_rejects():
    """Every dispatch key must be a name validate_command lets through."""
    assert set(TOOLS) - set(ARGUMENTS) == set()


def test_every_mapped_handler_exists():
    missing = sorted(
        name
        for name in set(TOOLS.values())
        if not callable(getattr(AdaptiveConsultationEngineExecute, name, None))
    )
    assert missing == []


def test_aliases_share_one_handler():
    """inspect_case and list_questions answer the same question."""
    assert TOOLS["inspect_case"] == TOOLS["list_questions"]
