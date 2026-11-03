"""Property tests for the two boundaries that hand-written cases under-cover.

Example-based tests only reach the inputs someone thought of. These two places
are where "someone didn't think of it" is expensive:

* ``validate_adaptive_question`` decides what a model may put on a patient's
  screen. It must reject *everything* it cannot vouch for, and it must reject it
  as ``AdaptiveQuestionError`` — any other exception escaping is a crash in a
  route handler, not a refusal.
* ``safe_bytes`` is the workspace's path-traversal guard. Its promise is
  "accepted means confined": if it returns bytes, the bytes came from inside
  the root.

Neither is a place to be clever. The properties are deliberately blunt.
"""
from __future__ import annotations

import string
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from medagent.dialogue.adaptive_questions import (
    AdaptiveQuestionError,
    validate_adaptive_question,
)
from medagent.execution import ExecutionError
from medagent.execution.workspace_files import safe_bytes

SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- what the model may publish -------------------------------------------


def arbitrary_value():
    """Anything a JSON body could contain, nested a couple of levels deep."""
    scalar = (
        st.none()
        | st.booleans()
        | st.integers()
        | st.floats(allow_nan=True, allow_infinity=True)
        | st.text()
        | st.binary().map(lambda b: b.decode("latin-1"))
    )
    return st.recursive(
        scalar,
        lambda children: st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=8), children, max_size=3),
        max_leaves=6,
    )


@SETTINGS
@given(arbitrary_value())
def test_validation_never_leaks_an_unexpected_exception(value):
    """Rejection must be a refusal, not a crash.

    A `KeyError` or `TypeError` escaping here becomes a 500 with no error code,
    and the browser has nothing to show the patient.
    """
    try:
        validate_adaptive_question(value)
    except AdaptiveQuestionError:
        return
    except Exception as exc:  # noqa: BLE001 - the point is to catch everything
        pytest.fail(f"{type(exc).__name__} escaped validation: {exc!r} for {value!r}")


@SETTINGS
@given(
    st.dictionaries(
        st.sampled_from(["topic", "question", "selection_mode", "options", "allow_other"]),
        arbitrary_value(),
        max_size=5,
    )
)
def test_a_well_shaped_dict_also_only_raises_the_expected_error(value):
    """The realistic shape (right key names, arbitrary values) is the risky one:
    the field-set check passes and the value checks then run."""
    try:
        validate_adaptive_question(value)
    except AdaptiveQuestionError:
        return
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"{type(exc).__name__} escaped validation: {exc!r} for {value!r}")


@SETTINGS
@given(st.text())
def test_topic_text_is_either_accepted_or_refused_never_crashed(topic):
    """Free text in a bounded field must never raise anything but the refusal."""
    value = {
        "topic": topic,
        "question": "现在疼得怎么样？",
        "selection_mode": "single",
        "allow_other": False,
        "options": [{"id": "a", "label": "轻微", "response_text": "轻微疼痛"}],
    }
    try:
        result = validate_adaptive_question(value)
    except AdaptiveQuestionError:
        return
    assert isinstance(result["topic"], str)
    assert result["topic"] == " ".join(topic.split())


# --- what the workspace will hand back ------------------------------------

#: Components that must never be accepted, however they are assembled.
FORBIDDEN_COMPONENTS = ["..", ".", "", " ", "\x00"]


@SETTINGS
@given(
    st.lists(st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=6), max_size=3),
    st.sampled_from(FORBIDDEN_COMPONENTS),
    st.booleans(),
)
def test_a_dangerous_path_component_is_always_refused(tmp_path, parts, bad, trailing):
    relative = "/".join(parts + [bad] + (["file"] if trailing else []))
    with pytest.raises(ExecutionError):
        safe_bytes(tmp_path, relative)


@SETTINGS
@given(st.text(min_size=1))
def test_an_absolute_or_backslashed_path_is_refused(tmp_path, text):
    """Absolute paths and Windows separators are refused regardless of content."""
    with pytest.raises(ExecutionError):
        safe_bytes(tmp_path, "/" + text)
    with pytest.raises(ExecutionError):
        safe_bytes(tmp_path, text.replace("/", "\\") + "\\x")


@SETTINGS
@given(
    st.lists(
        st.text(alphabet=string.ascii_lowercase + string.digits + "_-", min_size=1, max_size=8),
        min_size=1,
        max_size=3,
    )
)
def test_anything_accepted_really_came_from_inside_the_root(tmp_path, parts):
    """The guard's promise: if it returns bytes, they are the file we planted.

    Each accepted path is created under a fresh root with a unique body, so a
    traversal that silently escaped would either fail to find the file or read
    a different one.
    """
    # A fresh root per example: hypothesis reuses `tmp_path`, so two examples
    # that both use "0" would collide (a file where the next one wants a dir).
    root = tmp_path / uuid4().hex
    root.mkdir()
    body = ("|".join(parts)).encode()
    target = root.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    data, _revision = safe_bytes(root, "/".join(parts))
    assert data == body
