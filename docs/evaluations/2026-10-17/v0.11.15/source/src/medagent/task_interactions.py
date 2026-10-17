"""Host event identities for UI drafts; never measurement or authorization proof."""
from copy import deepcopy


def task_with_interaction(task, session_id, action_id, events):
    result = deepcopy(task)
    offered = {
        f"offer:{event.event_id}"
        for event in events
        if event.session_id == session_id
        and event.event_type == "self_check.offered"
        and event.payload.get("task_id") == task["id"]
    }
    repeat_id = (task.get("repeat") or {}).get("attempt_id")
    if repeat_id:
        valid_repeat = any(
            event.session_id == session_id
            and event.event_type == "self_check.repeat_started"
            and event.payload.get("task_id") == task["id"]
            and event.payload.get("id") == repeat_id
            for event in events
        )
        identity = f"repeat:{repeat_id}" if valid_repeat else f"action:{action_id}"
    elif (
        isinstance(task.get("interaction_id"), str)
        and task["interaction_id"] in offered
    ):
        # A host-owned continuation may intentionally restore an earlier offer.
        identity = task["interaction_id"]
    else:
        identity = f"action:{action_id}"
        for event in reversed(events):
            if event.session_id != session_id:
                continue
            # A restored host reply can point to an earlier offer. Subsequent
            # validation feedback must follow that visible occurrence, while a
            # later new offer still starts fresh.
            if event.event_type == "turn.agent_reply":
                prior = event.payload.get("reply") or {}
                prior_task = prior.get("task") or {}
                candidate = prior_task.get("interaction_id")
                if (
                    prior.get("kind") == "self_check"
                    and prior_task.get("id") == task["id"]
                    and isinstance(candidate, str)
                    and candidate in offered
                ):
                    identity = candidate
                    break
            if (
                event.event_type == "self_check.offered"
                and f"offer:{event.event_id}" in offered
            ):
                identity = f"offer:{event.event_id}"
                break
    result["interaction_id"] = identity
    return result
