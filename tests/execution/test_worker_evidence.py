"""Real host calculation receipts retain their workspace, turn and version scope."""

from copy import deepcopy
from uuid import uuid4

import pytest

from medagent.execution.capabilities import dispatch
from medagent.execution.worker_protocol import calculation_receipt
from tests.support.execution_case import payload


@pytest.fixture
def receipt():
    request = payload()
    result = dispatch(request)
    return request, {"turn_id": request["turn_id"], "result": result}


def test_host_receipt_is_accepted_without_mutating_the_record_or_binding(receipt):
    request, record = receipt
    original = deepcopy((request, record))
    result = calculation_receipt(
        record,
        workspace_id=request["workspace_id"],
        turn_ids={request["turn_id"]},
        capability_binding=request["binding"],
    )
    assert result["data"]["value"] == "1250"
    assert (request, record) == original


@pytest.mark.parametrize(
    "wrong_scope",
    ["workspace", "event_turn", "unknown_turn", "registry", "implementation", "no_binding"],
)
def test_a_valid_receipt_cannot_be_reused_outside_its_original_scope(receipt, wrong_scope):
    request, record = receipt
    workspace, turns, binding = request["workspace_id"], {request["turn_id"]}, request["binding"]
    if wrong_scope == "workspace":
        workspace = str(uuid4())
    elif wrong_scope == "event_turn":
        record["turn_id"] = str(uuid4())
    elif wrong_scope == "unknown_turn":
        turns = {str(uuid4())}
    elif wrong_scope == "no_binding":
        binding = None
    else:
        binding[wrong_scope + "_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        calculation_receipt(
            record, workspace_id=workspace, turn_ids=turns, capability_binding=binding
        )
