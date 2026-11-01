"""Shared fixtures for the test suite.

Two things every HTTP test used to re-implement, each time slightly differently:

* scrubbing ``MEDAGENT_*`` from the environment before building a runtime, so a
  developer's real model configuration cannot leak into a test;
* starting a demo-backed server on an ephemeral port, in a thread, with a
  temp directory that has to be torn down in the right order.

Both live here now. They are deliberately *not* autouse: scrubbing the
environment for all 1886 tests would silently change tests that set those
variables themselves. Ask for them by name.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def clean_medagent_env():
    """A process environment with every ``MEDAGENT_*`` variable removed.

    Only the ``MEDAGENT_`` prefix is scrubbed, not the whole environment:
    clearing everything would also drop ``no_proxy``, and once urllib has built
    its global opener with this machine's egress proxy, requests to 127.0.0.1
    would be proxied and killed.
    """
    scrubbed = {key: value for key, value in os.environ.items() if not key.startswith("MEDAGENT_")}
    with patch.dict(os.environ, scrubbed, clear=True):
        yield


@pytest.fixture
def demo_server(clean_medagent_env):
    """A running demo-backed HTTP server; yields its base URL.

    The demo backend keeps the suite offline and deterministic. Teardown order
    matters: shut the socket down before stopping the runtime, or a request
    thread can still be inside the engine when it is torn out.
    """
    from medagent.server import Handler, MedicalAgentServer
    from medagent.server.runtime import build_runtime

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = build_runtime(
            backend="demo",
            session_dir=root / "sessions",
            event_path=root / "events.jsonl",
        )
        context = runtime.start()
        httpd = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            context.services.require("consultation"),
            context.services.require("events"),
            "demo",
        )
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            worker.join(timeout=2)
            httpd.server_close()
            runtime.stop()


@pytest.fixture(scope="session")
def demo_server_factory():
    """A module-scoped server for read-mostly suites.

    Starting a server costs about a second, so a class or module that only
    reads state can share one. Tests that mutate host configuration must use
    the per-test ``demo_server`` instead — they would otherwise see each
    other's writes.
    """
    from medagent.server import Handler, MedicalAgentServer
    from medagent.server.runtime import build_runtime

    scrubbed = {key: value for key, value in os.environ.items() if not key.startswith("MEDAGENT_")}
    env = patch.dict(os.environ, scrubbed, clear=True)
    env.start()
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    runtime = build_runtime(
        backend="demo",
        session_dir=root / "sessions",
        event_path=root / "events.jsonl",
    )
    context = runtime.start()
    httpd = MedicalAgentServer(
        ("127.0.0.1", 0),
        Handler,
        context.services.require("consultation"),
        context.services.require("events"),
        "demo",
    )
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        worker.join(timeout=2)
        httpd.server_close()
        runtime.stop()
        tmp.cleanup()
        env.stop()
