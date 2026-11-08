"""Deployment composition must resolve adapters from the project checkout."""

from pathlib import Path
from unittest.mock import patch

from medagent.server.runtime import build_runtime


def test_desktop_dsh_backend_resolves_a_real_bridge_after_server_package_move():
    """Construct the service graph without making any provider request."""
    config = {
        "MEDAGENT_LLM_BASE_URL": "http://127.0.0.1:9/anthropic",
        "MEDAGENT_LLM_API_KEY": "composition-fixture",
        "MEDAGENT_LLM_MODEL": "fixture",
    }
    with patch("medagent.server.runtime.DshBackend") as backend:
        host = build_runtime(backend="dsh", config=config)
        context = host.start()
        try:
            context.services.require("consultation")
            root = backend.call_args.args[1]
            assert root == Path(__file__).resolve().parents[2]
            assert (root / "integrations/dsh/bridge.mjs").is_file()
            assert (root / "package.json").is_file()
        finally:
            host.stop()
