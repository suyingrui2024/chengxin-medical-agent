"""Parse real SDK delegation receipts in deterministic provider fixtures."""

import json
import re


def started_child(body):
    """Return the latest actual SDK child ID, never a fixed or invented ID."""
    ids = re.findall(r"started subagent ([\w-]+)", json.dumps(body["messages"]))
    if not ids:
        raise AssertionError("No SDK subagent activation receipt in the model request")
    return ids[-1]


def wait_child(body, timeout_ms=10000):
    """Build an explicit wait for the child actually started by this fixture."""
    return {
        "name": "wait_subagent",
        "input": {"agent_id": started_child(body), "timeout_ms": timeout_ms},
    }


def wait_results(body):
    """Read typed wait receipts rendered by the real runtime, in message order."""
    results = []

    def visit(value):
        if isinstance(value, list):
            for part in value:
                visit(part)
        elif isinstance(value, dict):
            if value.get("type") == "text":
                try:
                    parsed = json.loads(value["text"])
                except (ValueError, KeyError):
                    return
                if isinstance(parsed, dict) and parsed.get("schema") == "execution-subagent/v1":
                    results.append(parsed)
            else:
                for part in value.values():
                    visit(part)

    visit(body["messages"])
    return results
