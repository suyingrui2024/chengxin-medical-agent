"""The session events route's incremental cursor.

The page polls ``/api/sessions/<id>/events`` about once a second while a turn
runs, and one real session already carries ~3 MB of model payload. The route
therefore accepts ``?since=<n>`` and answers with only the events after that
offset; the page appends them instead of re-parsing the whole history.

Two properties matter and are tested here:

* a cursor inside the list is a delta — never a full resend;
* anything the server cannot honour as a delta (no cursor, garbage cursor, a
  cursor past the end after a reset) is a full list, so the client replaces
  its copy rather than appending onto a history the server no longer has.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


def get(base, path):
    try:
        with urlopen(Request(base + path), timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def new_session(base):
    with urlopen(Request(base + "/api/sessions", data=b"", method="POST"), timeout=10) as response:
        return json.loads(response.read())["session_id"]


@pytest.fixture
def session_with_events(demo_server):
    session_id = new_session(demo_server)
    status, body = get(demo_server, f"/api/sessions/{session_id}/events")
    assert status == 200
    assert body["total"] >= 1, "a new session must already have created events"
    return demo_server, session_id, body["total"]


def test_without_a_cursor_the_route_sends_everything(session_with_events):
    base, session_id, total = session_with_events
    status, body = get(base, f"/api/sessions/{session_id}/events")

    assert status == 200
    assert body["mode"] == "full"
    assert body["total"] == total
    assert len(body["events"]) == total


def test_a_cursor_inside_the_list_sends_only_what_is_new(session_with_events):
    """The regression this route exists to avoid: resending 3 MB every second."""
    base, session_id, total = session_with_events
    if total < 2:
        pytest.skip("needs at least two events to split the list")

    status, body = get(base, f"/api/sessions/{session_id}/events?since=1")

    assert status == 200
    assert body["mode"] == "delta"
    assert body["total"] == total
    assert len(body["events"]) == total - 1


def test_a_cursor_at_the_end_sends_an_empty_delta(session_with_events):
    base, session_id, total = session_with_events

    status, body = get(base, f"/api/sessions/{session_id}/events?since={total}")

    assert status == 200
    assert body["mode"] == "delta"
    assert body["events"] == []
    assert body["total"] == total


@pytest.mark.parametrize("cursor", ["999", "abc", "-1", "1.5", "", "0"])
def test_a_cursor_the_server_cannot_honour_is_a_full_list(
    session_with_events, cursor
):
    """A stale or malformed cursor must reset the client, not silently empty it.

    ``999`` is the reset case: the session's events shrank below the offset the
    page remembers, so a delta would leave the page with history the server no
    longer has. ``0`` is the first load.
    """
    base, session_id, total = session_with_events

    status, body = get(base, f"/api/sessions/{session_id}/events?since={cursor}")

    assert status == 200
    assert body["mode"] == "full"
    assert len(body["events"]) == total
