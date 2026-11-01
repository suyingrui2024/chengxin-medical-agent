"""Validating a browser's answer to a model-authored question.

`project_selected_answer` is the boundary between whatever the browser posts
back and what the engine records: it re-derives the answer text from the
*currently published* spec, so a stale tab, a hand-edited payload or a
tampered option id cannot smuggle text into the case. Every branch either
returns a projection or raises `AdaptiveQuestionError` — there is no "pass it
through and hope" path, which is exactly why each one needs a test.

The spec is built directly rather than through `validate_adaptive_question`:
the subject here is the *selection* contract, and going through the validator
would make every failure ambiguous between "the spec was rejected" and "the
answer was rejected".
"""
from __future__ import annotations

import pytest

from medagent.dialogue.adaptive_questions import (
    AdaptiveQuestionError,
    project_selected_answer,
    public_question,
)


def option(
    identity: str,
    label: str,
    *,
    response: str | None = None,
    tags: tuple[str, ...] = (),
    exclusive: bool = False,
    requires_detail: bool = False,
) -> dict:
    return {
        "id": identity,
        "label": label,
        "response_text": label if response is None else response,
        "risk_tags": list(tags),
        "exclusive": exclusive,
        "requires_detail": requires_detail,
    }


def spec(mode: str = "single", *, allow_other: bool = False, options=None) -> dict:
    return {
        "topic": "主题",
        "question": "问题",
        "selection_mode": mode,
        "allow_other": allow_other,
        "options": [option("a", "选项A"), option("b", "选项B")]
        if options is None
        else options,
    }


# --- not an adaptive answer at all -----------------------------------------


@pytest.mark.parametrize("choice", [None, "", "confirm_summary", "adaptive"])
def test_a_non_adaptive_choice_is_not_projected(choice):
    """Anything that is not an ``adaptive_*`` action is somebody else's answer."""
    assert project_selected_answer(spec(), choice, "随便写") is None


# --- "other" free text -----------------------------------------------------


def test_other_text_is_recorded_verbatim():
    result = project_selected_answer(
        spec(allow_other=True), "adaptive_other", "  说不清楚，就是难受  "
    )
    assert result == {
        "text": "说不清楚，就是难受",
        "selected": [],
        "risk_tags": [],
        "kind": "other",
    }


@pytest.mark.parametrize(
    "allow_other,text",
    [
        (False, "有内容"),          # question did not offer "other"
        (True, "   "),              # whitespace only
        (True, ""),                 # nothing typed
        (True, "字" * 2001),        # beyond the length bound
    ],
)
def test_other_text_is_rejected_when_unusable(allow_other, text):
    with pytest.raises(AdaptiveQuestionError, match="其他情况需要填写有效内容"):
        project_selected_answer(spec(allow_other=allow_other), "adaptive_other", text)


# --- "detail" answers ------------------------------------------------------


def detail_spec(**kwargs) -> dict:
    return spec(
        options=[option("a", "选项A", response="选项A，需要补充", requires_detail=True)],
        **kwargs,
    )


def test_detail_answer_gains_the_option_prefix_when_the_patient_did_not_type_it():
    result = project_selected_answer(detail_spec(), "adaptive_detail:a", "疼了两天")
    assert result == {
        "text": "选项A，需要补充：疼了两天",
        "selected": ["a"],
        "risk_tags": [],
        "kind": "detail",
    }


def test_detail_answer_is_not_prefixed_twice():
    """A pre-filled editor may already start with the option's own wording."""
    result = project_selected_answer(
        detail_spec(), "adaptive_detail:a", "选项A，需要补充：疼了两天"
    )
    assert result["text"] == "选项A，需要补充：疼了两天"


@pytest.mark.parametrize(
    "choice,text,match",
    [
        ("adaptive_detail:zzz", "有内容", "补充回答与当前选项不匹配"),   # unknown id
        ("adaptive_detail:a", "   ", "补充回答与当前选项不匹配"),        # no detail
    ],
)
def test_detail_answer_is_rejected_when_it_does_not_match(choice, text, match):
    with pytest.raises(AdaptiveQuestionError, match=match):
        project_selected_answer(detail_spec(), choice, text)


def test_detail_answer_is_rejected_for_an_option_that_takes_no_detail():
    plain = spec(options=[option("a", "选项A")])
    with pytest.raises(AdaptiveQuestionError, match="补充回答与当前选项不匹配"):
        project_selected_answer(plain, "adaptive_detail:a", "多写了")


# --- single selection ------------------------------------------------------


def test_single_selection_projects_the_published_wording():
    """The recorded text is the spec's, never the browser's."""
    result = project_selected_answer(spec("single"), "adaptive_single:a", "选项A")
    assert result == {
        "text": "选项A",
        "selected": ["a"],
        "risk_tags": [],
        "kind": "single",
    }


def test_single_selection_is_rejected_against_a_multiple_question():
    with pytest.raises(AdaptiveQuestionError, match="单选回答与当前问题不匹配"):
        project_selected_answer(spec("multiple"), "adaptive_single:a", "选项A")


def test_single_selection_with_an_unknown_option_is_stale():
    with pytest.raises(AdaptiveQuestionError, match="动态回答已经失效"):
        project_selected_answer(spec("single"), "adaptive_single:gone", "选项A")


# --- multiple selection ----------------------------------------------------


def test_multiple_selection_joins_the_published_wording():
    result = project_selected_answer(spec("multiple"), "adaptive_multiple:a,b", "选项A；选项B")
    assert result == {
        "text": "选项A；选项B",
        "selected": ["a", "b"],
        "risk_tags": [],
        "kind": "multiple",
    }


def test_multiple_selection_is_rejected_against_a_single_question():
    with pytest.raises(AdaptiveQuestionError, match="多选回答与当前问题不匹配"):
        project_selected_answer(spec("single"), "adaptive_multiple:a,b", "选项A；选项B")


@pytest.mark.parametrize(
    "choice",
    [
        "adaptive_multiple:",        # nothing selected
        "adaptive_multiple:a,a",     # the same option twice
    ],
)
def test_multiple_selection_must_name_distinct_options(choice):
    with pytest.raises(AdaptiveQuestionError, match="动态回答选择无效"):
        project_selected_answer(spec("multiple"), choice, "选项A")


def test_multiple_selection_collects_risk_tags_in_a_stable_order():
    options = [
        option("a", "胸闷", tags=("chest_discomfort",)),
        option("b", "冷汗", tags=("cold_sweat", "chest_discomfort")),
    ]
    result = project_selected_answer(
        spec("multiple", options=options), "adaptive_multiple:a,b", "胸闷；冷汗"
    )
    assert result["risk_tags"] == ["chest_discomfort", "cold_sweat"]


def test_an_option_that_needs_detail_cannot_be_submitted_in_a_multiple_question():
    options = [option("a", "选项A", requires_detail=True)]
    with pytest.raises(AdaptiveQuestionError, match="这个选项需要补充文字"):
        project_selected_answer(spec("multiple", options=options), "adaptive_multiple:a", "选项A")


def test_an_exclusive_option_cannot_be_combined_with_another():
    options = [option("a", "选项A", exclusive=True), option("b", "选项B")]
    with pytest.raises(AdaptiveQuestionError, match="互斥选项不能与其他选项同时提交"):
        project_selected_answer(
            spec("multiple", options=options), "adaptive_multiple:a,b", "选项A；选项B"
        )


def test_an_exclusive_option_on_its_own_is_allowed():
    options = [option("a", "选项A", exclusive=True)]
    result = project_selected_answer(
        spec("multiple", options=options), "adaptive_multiple:a", "选项A"
    )
    assert result["selected"] == ["a"]


# --- the posted text must match the spec -----------------------------------


def test_submitted_text_that_disagrees_with_the_spec_is_rejected():
    """This is the anti-tamper check: the browser cannot write its own answer."""
    with pytest.raises(AdaptiveQuestionError, match="动态回答文字与当前选项不一致"):
        project_selected_answer(spec("single"), "adaptive_single:a", "选项B")


def test_surrounding_whitespace_in_the_submitted_text_is_ignored():
    result = project_selected_answer(spec("single"), "adaptive_single:a", "  选项A\n")
    assert result["text"] == "选项A"


# --- unknown action --------------------------------------------------------


def test_an_unrecognised_adaptive_action_is_rejected():
    with pytest.raises(AdaptiveQuestionError, match="动态回答标识无效"):
        project_selected_answer(spec(), "adaptive_something_else", "文本")


# --- public_question -------------------------------------------------------


def test_public_question_hands_out_an_isolated_copy():
    """Serialising the question must not let a caller mutate the stored spec."""
    original = spec("multiple")
    copy = public_question(original)
    copy["options"][0]["label"] = "改过了"
    copy["topic"] = "改过了"
    assert original["options"][0]["label"] == "选项A"
    assert original["topic"] == "主题"
