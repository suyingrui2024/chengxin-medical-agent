"""All callers use the same cancellation-aware backend dispatch contract."""

from threading import Event
from unittest.mock import Mock

import pytest

from medagent.core.decision_wait import DecisionInterrupted, invoke_decision


def test_cancelled_turn_never_calls_the_model():
    backend = Mock()
    cancelled = Event()
    cancelled.set()
    with pytest.raises(DecisionInterrupted):
        invoke_decision(backend, "session", [], cancelled)
    backend.decide.assert_not_called()
    backend.decide_cancellable.assert_not_called()


def test_transport_receives_the_original_token_and_error():
    backend = Mock()
    cancelled = Event()
    failure = TimeoutError("fixture")
    backend.decide_cancellable.side_effect = failure
    with pytest.raises(TimeoutError) as caught:
        invoke_decision(backend, "session", [], cancelled)
    assert caught.value is failure
    backend.decide_cancellable.assert_called_once_with("session", [], cancelled)
    backend.decide.assert_not_called()


def test_offline_backend_with_only_decide_still_works():
    backend = Mock(spec=["decide"])
    backend.decide.return_value = {"tool": "fixture", "arguments": {}}
    assert invoke_decision(backend, "session", [], Event())["tool"] == "fixture"
