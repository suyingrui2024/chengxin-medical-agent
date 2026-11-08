"""Validate worker evidence against explicit session scope, without mutating it.

Transport parsing, evidence ownership and session state transitions have
different owners. These functions have no session or process reference and
cannot persist records, emit events, change a turn, or launch a tool.
"""

import json

from .capabilities import validate_receipt
from .errors import ExecutionError
from .observations import validate_observation


def decode_record(line):
    """Ignore console noise and non-object JSON; bound a single worker event."""
    if len(line) > 4_000_000:
        raise ExecutionError("执行事件过大")
    try:
        value = json.loads(line)
    except ValueError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("type", "runtime.unknown"), str):
        return None
    return value


def observation_batch(record, *, turn_ids):
    values = record["observations"]
    if not isinstance(values, list) or len(values) > 64:
        raise ValueError("Invalid observation batch")
    validated = [validate_observation(value) for value in values]
    if any(value["turn_id"] not in turn_ids for value in validated):
        raise ValueError("Observation belongs to an unknown turn")
    return validated


def calculation_receipt(record, *, workspace_id, turn_ids, capability_binding):
    result = validate_receipt(record.get("result"))
    if (
        result["workspace_id"] != workspace_id
        or result["turn_id"] != record.get("turn_id")
        or result["turn_id"] not in turn_ids
    ):
        raise ValueError("Calculation belongs to another workspace or turn")
    if not capability_binding or any(
        result[key] != capability_binding[key]
        for key in ["registry_sha256", "implementation_sha256"]
    ):
        raise ValueError("Calculation does not match the pinned implementation")
    return result
