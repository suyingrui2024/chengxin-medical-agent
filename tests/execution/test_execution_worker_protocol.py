"""The worker protocol reader.

`ExecutionSession._read` consumes the JSON lines an execution worker writes to
stdout and turns them into session state: turn status, jobs, observations,
capability receipts, clarifications. It cannot be exercised end to end on
macOS — `_start` refuses to run without Linux bubblewrap — so these tests drive
the reader directly with a fake process.

That is the honest seam. The reader is where untrusted subprocess output meets
session state, so every record type either updates state deliberately or is
dropped with an event; there is no path that trusts a line because it parsed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from medagent.execution import ExecutionError
from medagent.execution.session import ExecutionSession

SETTINGS = {
    "MEDAGENT_LLM_BASE_URL": "http://127.0.0.1:1",
    "MEDAGENT_LLM_API_KEY": "test-key",
    "MEDAGENT_LLM_MODEL": "test-model",
}


class FakeProcess:
    """Just enough of a subprocess for the reader: iterate lines, then close."""

    def __init__(self, *lines: str, exit_code: int | None = None):
        self._lines = list(lines)
        self.stdout = self
        self._exit = exit_code
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True

    def poll(self):
        return self._exit


class ScriptedProcess(FakeProcess):
    """A process whose lines are produced lazily, so a test can look at the
    session *between* records.  The reader pulls one line, handles it, then
    asks for the next — so code after a ``yield`` observes that line's effect.
    """

    def __init__(self, script, exit_code: int | None = None):
        self._script = script
        self.stdout = self
        self._exit = exit_code
        self.closed = False

    def __iter__(self):
        return self._script()


def records(*payloads: dict) -> list[str]:
    return [json.dumps(payload, ensure_ascii=False) for payload in payloads]


@pytest.fixture
def session(tmp_path):
    s = ExecutionSession("sess-1", tmp_path, Path.cwd(), SETTINGS)
    yield s
    if s.directory_fd is not None:
        os.close(s.directory_fd)
        s.directory_fd = None


def run(session, process):
    session.process = process
    session._read(process)


def with_turn(session, status="starting"):
    """A turn whose id is a UUID — the worker protocol identifies turns that way."""
    turn_id = str(uuid4())
    session.turn_id = turn_id
    session.turns = [{"id": turn_id, "status": status, "text": "任务"}]
    return turn_id


# --- lifecycle -------------------------------------------------------------


def test_ready_sets_the_ready_event(session):
    assert not session.ready.is_set()
    run(session, FakeProcess(*records({"type": "ready"})))
    assert session.ready.is_set()


def test_turn_started_marks_the_session_running(session):
    """Observed mid-stream: once the worker exits, a running session is failed."""
    turn_id = with_turn(session)
    seen = []

    def script():
        yield json.dumps({"type": "turn.started", "turn_id": turn_id}) + "\n"
        seen.append(session.status)

    run(session, ScriptedProcess(script))
    assert seen == ["running"]


def test_turn_completed_records_the_final_response(session):
    turn_id = with_turn(session)
    run(
        session,
        FakeProcess(
            *records(
                {"type": "turn.started", "turn_id": turn_id},
                {"type": "turn.completed", "turn_id": turn_id, "final_response": "做完了"},
            )
        ),
    )
    assert session.status == "completed"
    assert session.final_response == "做完了"
    assert session.turns[-1]["status"] == "completed"


def test_turn_failed_records_the_error_type(session):
    turn_id = with_turn(session)
    run(
        session,
        FakeProcess(
            *records({"type": "turn.failed", "turn_id": turn_id, "error_type": "provider_failed"})
        ),
    )
    assert session.status == "failed"
    assert session.error == "provider_failed"
    assert session.turns[-1]["status"] == "failed"


def test_turn_cancelled_is_not_a_failure(session):
    turn_id = with_turn(session)
    run(session, FakeProcess(*records({"type": "turn.cancelled", "turn_id": turn_id})))
    assert session.status == "cancelled"


def test_a_rejected_turn_is_reported_as_failed(session):
    turn_id = with_turn(session)
    run(session, FakeProcess(*records({"type": "rejected", "turn_id": turn_id})))
    assert session.status == "failed"


def test_worker_error_sets_the_diagnostic_and_releases_the_waiter(session):
    run(session, FakeProcess(*records({"type": "worker.error", "diagnostic": "启动失败"})))
    assert session.error == "启动失败"
    assert session.ready.is_set()


def test_a_deadline_puts_the_turn_into_cancelling(session):
    """A deadline starts a cancellation and names the reason; the worker then
    exits, so the turn ends up interrupted rather than still cancelling."""
    turn_id = with_turn(session, status="running")
    seen = []

    def script():
        yield json.dumps({"type": "turn.deadline", "turn_id": turn_id}) + "\n"
        seen.append((session.status, session.turns[-1]["status"], session.error))

    run(session, ScriptedProcess(script))
    assert seen == [("cancelling", "cancelling", "time_budget_exhausted")]
    # The exit message wins in the end: the worker really did stop.
    assert session.error == "执行进程意外结束"
    assert session.turns[-1]["status"] == "interrupted"


# --- jobs and model calls --------------------------------------------------


def test_a_jobs_snapshot_replaces_the_job_and_agent_lists(session):
    seen = []

    def script():
        yield (
            json.dumps(
                {"type": "jobs.snapshot", "jobs": [{"id": "j1"}], "agents": [{"id": "a1"}]}
            )
            + "\n"
        )
        seen.append((list(session.jobs), list(session.agents)))

    run(session, ScriptedProcess(script))
    assert seen == [([{"id": "j1"}], [{"id": "a1"}])]
    # The agent list is cleared once the worker exits; the job list is not.
    assert session.agents == []


def test_cancelling_jobs_clears_them_and_cancels_the_turn(session):
    turn_id = with_turn(session, status="cancelling")
    session.jobs = [{"id": "j1"}]
    session.agents = [{"id": "a1"}]
    run(
        session,
        FakeProcess(
            *records({"type": "jobs.cancelled", "turn_id": turn_id, "reason": "user_cancelled"})
        ),
    )
    assert session.jobs == []
    assert session.agents == []
    assert session.turns[-1]["status"] == "cancelled"


def test_a_model_request_bumps_the_turn_call_count(session):
    turn_id = with_turn(session)
    session.turns[-1]["model_calls"] = 2
    run(
        session,
        FakeProcess(
            *records(
                {"type": "model.request", "turn_id": turn_id, "index": 5},
                {"type": "model.request_started", "turn_id": turn_id, "index": 3},
            )
        ),
    )
    # max(2, 5) then max(5, 3) — the counter never goes backwards
    assert session.turns[-1]["model_calls"] == 5


def test_the_resume_flag_is_set_from_a_turn_start_notification(session):
    assert session.require_resume is False
    run(
        session,
        FakeProcess(
            *records(
                {
                    "type": "runtime.notification",
                    "notification": {
                        "method": "session.event",
                        "params": {
                            "sessionId": session.id,
                            "event": {"type": "turn/start"},
                        },
                    },
                }
            )
        ),
    )
    assert session.require_resume is True


def test_a_notification_for_another_session_does_not_set_the_resume_flag(session):
    run(
        session,
        FakeProcess(
            *records(
                {
                    "type": "runtime.notification",
                    "notification": {
                        "method": "session.event",
                        "params": {"sessionId": "别的会话", "event": {"type": "turn/start"}},
                    },
                }
            )
        ),
    )
    assert session.require_resume is False


# --- observations ----------------------------------------------------------


def observation(turn_id: str) -> dict:
    return {
        "schema": "execution-observation/v1",
        "epoch": str(uuid4()),
        "sequence": 1,
        "session_id": str(uuid4()),
        "call_id": str(uuid4()),
        "sdk_turn": 1,
        "sdk_step": 1,
        "call_sequence": 1,
        "tool": "bash",
        "state": "completed",
        "exit_code": 0,
        "signal": None,
        "job_id": None,
        "job_kind": None,
        "wait_ms": 5,
        "turn_id": turn_id,
    }


def test_a_valid_observation_is_recorded(session):
    turn_id = with_turn(session)
    run(
        session,
        FakeProcess(
            *records({"type": "tool.observations", "observations": [observation(turn_id)]})
        ),
    )
    assert len(session.observations) == 1
    assert session.observations[0]["turn_id"] == turn_id


def test_an_observation_for_an_unknown_turn_is_refused(session):
    """A worker cannot invent history by claiming an observation of its own."""
    with_turn(session)
    run(
        session,
        FakeProcess(
            *records({"type": "tool.observations", "observations": [observation(str(uuid4()))]})
        ),
    )
    assert session.observations == []
    assert any(event["type"] == "tool.observation_unavailable" for event in session.events)


def test_a_malformed_observation_is_refused(session):
    turn_id = with_turn(session)
    broken = observation(turn_id)
    broken["state"] = "not_a_state"
    run(session, FakeProcess(*records({"type": "tool.observations", "observations": [broken]})))
    assert session.observations == []
    assert any(event["type"] == "tool.observation_unavailable" for event in session.events)


def test_the_observation_history_is_bounded(session):
    turn_id = with_turn(session)
    session.observations = [
        {**observation(turn_id), "observation_id": f"old-{i}"} for i in range(128)
    ]
    run(
        session,
        FakeProcess(
            *records({"type": "tool.observations", "observations": [observation(turn_id)]})
        ),
    )
    assert len(session.observations) == 128
    assert session.history_trimmed is True


# --- capability receipts ---------------------------------------------------


def test_an_invalid_capability_receipt_is_refused(session):
    """A receipt that does not validate must never reach the calculation log."""
    turn_id = with_turn(session)
    run(
        session,
        FakeProcess(
            *records(
                {"type": "capability.completed", "turn_id": turn_id, "result": {"nope": True}}
            )
        ),
    )
    assert session.calculations == []
    assert any(event["type"] == "capability.failed" for event in session.events)


# --- malformed input -------------------------------------------------------


def test_unparsable_lines_are_skipped_without_disturbing_state(session):
    turn_id = with_turn(session)
    run(
        session,
        FakeProcess(
            "这不是 JSON\n",
            "\n",
            *records({"type": "turn.completed", "turn_id": turn_id, "final_response": "好"}),
        ),
    )
    assert session.status == "completed"


@pytest.mark.parametrize("noise", ["null", "[]", '"console text"', "12", '{"type": []}'])
def test_non_record_json_cannot_interrupt_the_following_valid_event(session, noise):
    turn_id = with_turn(session)
    run(session, FakeProcess(noise, *records({
        "type": "turn.completed", "turn_id": turn_id, "final_response": "valid completion",
    })))
    assert session.status == "completed"
    assert session.final_response == "valid completion"


def test_an_oversized_event_is_rejected(session):
    with pytest.raises(ExecutionError, match="执行事件过大"):
        run(session, FakeProcess("x" * 4_000_001 + "\n"))


def test_a_replaced_worker_cannot_write_to_the_session(session):
    """A restarted worker must not be able to mutate state through the old reader."""
    turn_id = with_turn(session)
    stale = FakeProcess(
        *records({"type": "turn.completed", "turn_id": turn_id, "final_response": "旧"})
    )
    session.process = FakeProcess()  # the live worker is a different object
    session._read(stale)
    assert session.status == "created"
    assert session.final_response == ""


# --- worker exit -----------------------------------------------------------


def test_a_worker_that_exits_mid_turn_fails_the_session(session):
    turn_id = with_turn(session, status="running")
    process = FakeProcess(*records({"type": "turn.started", "turn_id": turn_id}), exit_code=1)
    run(session, process)
    assert session.status == "failed"
    assert session.error == "执行进程意外结束"
    assert session.turns[-1]["status"] == "interrupted"
    assert process.closed is True
    assert any(event["type"] == "worker.exited" for event in session.events)
    assert session.ready.is_set()


def test_a_worker_that_exits_after_completion_is_left_alone(session):
    turn_id = with_turn(session)
    process = FakeProcess(
        *records({"type": "turn.completed", "turn_id": turn_id, "final_response": "好"}),
        exit_code=0,
    )
    run(session, process)
    assert session.status == "completed"
    assert not any(event["type"] == "worker.exited" for event in session.events)
