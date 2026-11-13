"""Wait for bounded decision I/O without holding the clinical session lock."""

from dataclasses import dataclass, field
from threading import BoundedSemaphore, Condition, Event, Thread
from uuid import uuid4
import logging


class DecisionInterrupted(RuntimeError):
    pass


class DecisionCapacityError(RuntimeError):
    pass


def invoke_decision(backend, session_id, messages, cancelled=None):
    """Single dispatch for foreground and review model calls.

    Callers retain their own deadlines, budgets and late-result handling;
    transports retain their retry policy. Legacy offline backends may expose
    only decide(), while real transports receive the same cancellation token.
    """
    if cancelled is not None and cancelled.is_set():
        raise DecisionInterrupted()
    cancellable = getattr(backend, "decide_cancellable", None)
    if cancelled is not None and callable(cancellable):
        return cancellable(session_id, messages, cancelled)
    return backend.decide(session_id, messages)


@dataclass
class DecisionTurn:
    identity: str = field(default_factory=lambda: str(uuid4()))
    cancelled: Event = field(default_factory=Event)
    turn_id: str = ""


class DecisionWaiter:
    def __init__(self, maximum=8):
        self.slots = BoundedSemaphore(maximum)

    def invoke(
        self, backend, session_id, messages, turn, condition: Condition, discarded
    ):
        """Run one model decision on a worker slot, or refuse when busy.

        The slot is acquired without blocking: a capacity error tells the
        engine to stop the turn rather than queue it, because a queued
        decision would finish after the patient stopped waiting.  A discarded
        result is logged exactly once, so the audit shows the model worked
        and the answer was thrown away.
        """
        if turn.cancelled.is_set():
            raise DecisionInterrupted()
        if not self.slots.acquire(blocking=False):
            raise DecisionCapacityError("Decision workers are busy")
        result = {"done": False, "discard_logged": False}

        def log_discard():
            if not result["discard_logged"]:
                result["discard_logged"] = True
                try:
                    discarded()
                except Exception as error:
                    logging.getLogger(__name__).error(
                        "Decision discard audit failed (%s)", type(error).__name__
                    )

        def work():
            try:
                result["value"] = invoke_decision(
                    backend, session_id, messages, turn.cancelled
                )
            except Exception as error:
                result["error"] = error
            finally:
                self.slots.release()
                with condition:
                    result["done"] = True
                    if turn.cancelled.is_set():
                        log_discard()
                    condition.notify_all()

        try:
            Thread(target=work, name="medical-decision", daemon=True).start()
        except Exception:
            self.slots.release()
            raise
        # Condition releases/restores every recursion level of its RLock. HTTP
        # response snapshots may retain an outer lock while the engine submits.
        condition.wait_for(lambda: result["done"] or turn.cancelled.is_set())
        if turn.cancelled.is_set():
            if result["done"]:
                log_discard()
            raise DecisionInterrupted()
        if "error" in result:
            raise result["error"]
        return result["value"]
