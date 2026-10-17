"""Bounded research execution observations; never clinical calculation receipts."""

import math
from uuid import UUID

ACTIVE_JOB_STATES = {"starting", "running", "stopping"}
FIELDS = {
    "schema",
    "epoch",
    "sequence",
    "session_id",
    "call_id",
    "sdk_turn",
    "sdk_step",
    "call_sequence",
    "tool",
    "state",
    "exit_code",
    "signal",
    "job_id",
    "job_kind",
    "wait_ms",
    "turn_id",
}
STATES = {
    "unavailable",
    "failed",
    "completed",
    "cancelled",
    "timed_out",
    "killed",
    "background",
    "promoted",
    "starting",
    "running",
    "stopping",
}


def validate_observation(value):
    if (
        not isinstance(value, dict)
        or set(value) - FIELDS - {"observation_id"}
        or FIELDS - set(value)
    ):
        raise ValueError("Invalid execution observation fields")
    if (
        value["schema"] != "execution-observation/v1"
        or not isinstance(value["tool"], str)
        or value["tool"] not in {"bash", "job_output"}
        or not isinstance(value["state"], str)
        or value["state"] not in STATES
    ):
        raise ValueError("Unsupported execution observation")
    for name in ["epoch", "turn_id"]:
        if (
            not isinstance(value[name], str)
            or len(value[name]) != 36
            or str(UUID(value[name])) != value[name]
        ):
            raise ValueError("Invalid observation owner")
    for name in ["sequence", "sdk_turn", "sdk_step", "call_sequence"]:
        if (
            type(value[name]) is not int
            or not 0 <= value[name] <= 2**53 - 1
            or (name == "sequence" and value[name] == 0)
        ):
            raise ValueError("Invalid observation cursor")
    for name in ["session_id", "call_id", "signal", "job_id", "job_kind"]:
        item = value[name]
        if item is None and name not in {"session_id", "call_id"}:
            continue
        if (
            not isinstance(item, str)
            or not 1 <= len(item) <= 256
            or any(ord(char) < 32 for char in item)
        ):
            raise ValueError("Invalid observation identity")
    exit_code = value["exit_code"]
    if exit_code is not None and (
        type(exit_code) is not int or not -(2**31) <= exit_code <= 2**32 - 1
    ):
        raise ValueError("Invalid process exit code")
    wait = value["wait_ms"]
    if wait is not None and (
        type(wait) not in {int, float}
        or not 0 <= wait <= 2**31 - 1
        or not math.isfinite(wait)
    ):
        raise ValueError("Invalid observation wait")
    identity = f"{value['epoch']}:{value['sequence']}"
    if "observation_id" in value and value["observation_id"] != identity:
        raise ValueError("Observation identity mismatch")
    return {**value, "observation_id": identity}
