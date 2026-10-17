"""Wait for bounded decision I/O without holding the clinical session lock."""

from dataclasses import dataclass, field
from threading import BoundedSemaphore, Condition, Event, Thread
from uuid import uuid4
import logging


class DecisionInterrupted(RuntimeError):
    pass


class DecisionCapacityError(RuntimeError):
    pass


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
                cancellable = getattr(backend, "decide_cancellable", None)
                result["value"] = (
                    cancellable(session_id, messages, turn.cancelled)
                    if callable(cancellable)
                    else backend.decide(session_id, messages)
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
