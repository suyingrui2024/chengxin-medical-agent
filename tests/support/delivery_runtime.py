"""The execution delivery runtime: a real manager over a local provider.

``DeliveryRuntimeTests`` used to be instantiated by the long-delivery check to
borrow its setUp; the runtime now lives here and both suites compose it.
``wait_until`` moved with it -- only the delivery family polls bounded
transitions.
"""
import tempfile
import time
from pathlib import Path

from medagent.execution import ExecutionManager
from tests.support.scripted import Provider


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Expected bounded execution transition did not occur")


class DeliveryRuntime:
    """A temporary ExecutionManager wired to a scripted local provider."""

    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = ExecutionManager(root=Path(self.temporary.name))
        self.provider = None

    def close(self):
        self.manager.close()
        if self.provider:
            self.provider.shutdown()
            self.provider.server_close()
        self.temporary.cleanup()

    def session(self, responses):
        self.provider = Provider(responses)
        self.settings = {
            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{self.provider.server_port}/anthropic",
            "MEDAGENT_LLM_API_KEY": "local-delivery-fixture",
            "MEDAGENT_LLM_MODEL": "fixture",
        }
        return self.manager.create(self.settings)

    def finish(self, session):
        wait_until(
            lambda: session.snapshot()["status"] in {"completed", "failed", "cancelled"}
        )
        return session.snapshot()

    def observations(self, session):
        return [
            value for value in session.observations if value["tool"] == "delivery_check"
        ]
