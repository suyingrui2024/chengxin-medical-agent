"""What the model is allowed to publish to a patient.

`validate_adaptive_question` is the gate between a model-authored question and
a patient's screen. It enforces the host's bounds (field set, lengths, option
count), the clinical-safety rules (no prohibited instructions, every risk tag
backed by visible wording that is not negated), and the shape the browser
renderer depends on.

Nothing in this file may be relaxed without a product decision: each rejection
below is a thing a patient must never see.
"""
from __future__ import annotations

import pytest

from medagent.dialogue.adaptive_questions import (
    MAX_OPTIONS,
    AdaptiveQuestionError,
    validate_adaptive_question,
)


def opt(identity: str, label: str, response: str, **over) -> dict:
    """An option whose response keeps the button wording, as the host requires."""
    item = {"id": identity, "label": label, "response_text": response}
    item.update(over)
    return item


def q(**over) -> dict:
    base = {
        "topic": "疼痛",
        "question": "现在疼得怎么样？",
        "selection_mode": "single",
        "allow_other": False,
        "options": [opt("mild", "轻微", "轻微疼痛")],
    }
    base.update(over)
    return base


# --- shape -----------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "字符串", [], 42])
def test_a_question_must_be_an_object(value):
    with pytest.raises(AdaptiveQuestionError, match="动态问题必须是对象"):
        validate_adaptive_question(value)


def test_missing_required_fields_are_rejected():
    broken = q()
    del broken["topic"]
    with pytest.raises(AdaptiveQuestionError, match="动态问题字段不完整"):
        validate_adaptive_question(broken)


def test_unknown_fields_are_rejected_rather_than_ignored():
    """An unrecognised field is a model mistake, not something to drop silently."""
    with pytest.raises(AdaptiveQuestionError, match="动态问题字段不完整"):
        validate_adaptive_question(q(surprise="x"))


# --- topic and question text ----------------------------------------------


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("topic", 123, "topic必须是文字"),
        ("topic", "   ", "topic长度无效"),
        ("topic", "长" * 81, "topic长度无效"),
        ("topic", "带\x00控制符", "topic包含控制字符"),
        ("question", None, "question必须是文字"),
        ("question", "问" * 241, "question长度无效"),
    ],
)
def test_text_bounds_are_enforced(field, value, match):
    with pytest.raises(AdaptiveQuestionError, match=match):
        validate_adaptive_question(q(**{field: value}))


def test_whitespace_is_normalised_not_rejected():
    result = validate_adaptive_question(q(topic="  疼  痛  "))
    assert result["topic"] == "疼 痛"


def test_an_unregistered_topic_is_rejected_when_a_whitelist_is_given():
    with pytest.raises(AdaptiveQuestionError, match="动态问题主题未注册"):
        validate_adaptive_question(q(topic="疼痛"), allowed_topics={"发热"})


def test_a_registered_topic_passes_the_whitelist():
    result = validate_adaptive_question(q(topic="疼痛"), allowed_topics={"疼痛", "发热"})
    assert result["topic"] == "疼痛"


@pytest.mark.parametrize("phrase", ["请自行停药", "可以排除心脏病", "已经确诊"])
def test_prohibited_instructions_in_the_question_are_rejected(phrase):
    with pytest.raises(AdaptiveQuestionError, match="动态问题包含未允许的医疗指令"):
        validate_adaptive_question(q(question=f"{phrase}，你觉得呢？"))


# --- selection mode --------------------------------------------------------


def test_an_unknown_selection_mode_is_rejected():
    with pytest.raises(AdaptiveQuestionError, match="动态问题选择模式无效"):
        validate_adaptive_question(q(selection_mode="ranked"))


def test_allow_other_must_be_a_real_boolean():
    with pytest.raises(AdaptiveQuestionError, match="allow_other必须是布尔值"):
        validate_adaptive_question(q(allow_other="yes"))


@pytest.mark.parametrize("options", ["不是列表", [opt("a", "轻微", "轻微疼痛")] * (MAX_OPTIONS + 1)])
def test_option_count_bounds_are_enforced(options):
    with pytest.raises(AdaptiveQuestionError, match="动态问题选项数量无效"):
        validate_adaptive_question(q(selection_mode="multiple", options=options))


def test_a_text_question_may_not_carry_options():
    with pytest.raises(AdaptiveQuestionError, match="文字问题不能同时提供选项"):
        validate_adaptive_question(q(selection_mode="text"))


def test_a_text_question_may_not_offer_other():
    with pytest.raises(AdaptiveQuestionError, match="文字问题直接使用输入框，无需其他选项"):
        validate_adaptive_question(q(selection_mode="text", options=[], allow_other=True))


def test_a_choice_question_needs_at_least_one_option():
    with pytest.raises(AdaptiveQuestionError, match="选择问题至少需要一个选项"):
        validate_adaptive_question(q(options=[]))


def test_a_text_question_validates_cleanly():
    result = validate_adaptive_question(q(selection_mode="text", options=[]))
    assert result["selection_mode"] == "text"
    assert result["options"] == []


# --- options ---------------------------------------------------------------


def test_an_option_must_be_an_object():
    with pytest.raises(AdaptiveQuestionError, match="动态选项必须是对象"):
        validate_adaptive_question(q(options=["轻微疼痛"]))


def test_option_fields_are_closed():
    with pytest.raises(AdaptiveQuestionError, match="动态选项字段无效"):
        validate_adaptive_question(q(options=[opt("a", "轻微", "轻微疼痛", colour="red")]))


def test_an_option_needs_the_host_required_fields():
    with pytest.raises(AdaptiveQuestionError, match="动态选项字段无效"):
        validate_adaptive_question(q(options=[{"id": "a", "label": "轻微"}]))


@pytest.mark.parametrize("identity", ["A", "1abc", "", "带空格 id", "x" * 41])
def test_option_ids_must_match_the_identifier_grammar(identity):
    with pytest.raises(AdaptiveQuestionError, match="动态选项ID无效"):
        validate_adaptive_question(q(options=[opt(identity, "轻微", "轻微疼痛")]))


def test_duplicate_option_ids_are_rejected():
    with pytest.raises(AdaptiveQuestionError, match="动态选项ID重复"):
        validate_adaptive_question(
            q(options=[opt("a", "轻微", "轻微疼痛"), opt("a", "明显", "明显疼痛")])
        )


def test_the_response_must_keep_the_button_wording():
    """The patient must be able to recognise their own words in the record."""
    with pytest.raises(AdaptiveQuestionError, match="选项回答必须保留患者看到的按钮原文"):
        validate_adaptive_question(q(options=[opt("a", "轻微", "有点疼")]))


def test_prohibited_instructions_in_an_option_are_rejected():
    with pytest.raises(AdaptiveQuestionError, match="动态选项包含未允许的医疗指令"):
        validate_adaptive_question(q(options=[opt("a", "建议服用", "建议服用布洛芬")]))


# --- risk tags -------------------------------------------------------------


@pytest.mark.parametrize(
    "tags,match",
    [
        ("不是列表", "动态选项风险标签无效"),
        (["not_a_tag"], "动态选项风险标签无效"),
        (["chest_discomfort"] * 2, "动态选项风险标签无效"),
        ([f"tag{i}" for i in range(5)], "动态选项风险标签无效"),
    ],
)
def test_risk_tags_are_validated(tags, match):
    with pytest.raises(AdaptiveQuestionError, match=match):
        validate_adaptive_question(
            q(options=[opt("a", "胸闷", "胸闷得厉害", risk_tags=tags)])
        )


def test_a_risk_tag_needs_visible_wording():
    with pytest.raises(AdaptiveQuestionError, match="风险标签没有可见文字依据"):
        validate_adaptive_question(
            q(options=[opt("a", "轻微", "轻微疼痛", risk_tags=["chest_discomfort"])])
        )


def test_a_negated_cue_does_not_support_a_risk_tag():
    """「没有胸闷」 mentions the cue but denies it — it must not raise an alarm."""
    with pytest.raises(AdaptiveQuestionError, match="风险标签没有可见文字依据"):
        validate_adaptive_question(
            q(options=[opt("a", "没有胸闷", "没有胸闷", risk_tags=["chest_discomfort"])])
        )


def test_a_supported_risk_tag_is_kept():
    result = validate_adaptive_question(
        q(options=[opt("a", "胸闷", "胸闷得厉害", risk_tags=["chest_discomfort"])])
    )
    assert result["options"][0]["risk_tags"] == ["chest_discomfort"]


# --- option behaviour flags ------------------------------------------------


@pytest.mark.parametrize("flag", ["exclusive", "requires_detail"])
def test_behaviour_flags_must_be_booleans(flag):
    with pytest.raises(AdaptiveQuestionError, match="动态选项行为标记无效"):
        validate_adaptive_question(
            q(options=[opt("a", "轻微", "轻微疼痛", **{flag: "yes"})])
        )


def test_a_multiple_choice_question_may_not_have_a_detail_option():
    """Multiple choice routes detail through the single "other" box instead."""
    with pytest.raises(AdaptiveQuestionError, match="多选问题请使用统一的其他情况输入"):
        validate_adaptive_question(
            q(selection_mode="multiple", options=[opt("a", "轻微", "轻微疼痛", requires_detail=True)])
        )


# --- the "other" box -------------------------------------------------------


def test_other_wording_is_bounded_when_enabled():
    result = validate_adaptive_question(q(allow_other=True))
    assert result["other_label"] == "其他情况"
    assert result["other_placeholder"] == "请补充具体情况"


def test_other_wording_may_not_be_set_when_other_is_disabled():
    with pytest.raises(AdaptiveQuestionError, match="未启用其他输入时不能设置相关文字"):
        validate_adaptive_question(q(allow_other=False, other_label="其他"))


def test_custom_other_wording_is_accepted_when_enabled():
    result = validate_adaptive_question(
        q(allow_other=True, other_label="还有别的情况", other_placeholder="说说看")
    )
    assert result["other_label"] == "还有别的情况"
    assert result["other_placeholder"] == "说说看"


# --- the detail box -------------------------------------------------------


def test_a_detail_option_gets_a_default_placeholder():
    result = validate_adaptive_question(
        q(options=[opt("a", "轻微", "轻微疼痛", requires_detail=True)])
    )
    assert result["options"][0]["detail_placeholder"] == "请补充具体情况"


def test_a_detail_option_may_set_its_own_placeholder():
    result = validate_adaptive_question(
        q(
            options=[
                opt(
                    "a",
                    "轻微",
                    "轻微疼痛",
                    requires_detail=True,
                    detail_placeholder="疼了多久？",
                )
            ]
        )
    )
    assert result["options"][0]["detail_placeholder"] == "疼了多久？"


def test_an_option_that_takes_no_detail_may_not_set_a_placeholder():
    with pytest.raises(AdaptiveQuestionError, match="无需补充的选项不能设置输入提示"):
        validate_adaptive_question(
            q(options=[opt("a", "轻微", "轻微疼痛", detail_placeholder="疼了多久？")])
        )


# --- a complete, valid question -------------------------------------------


def test_a_realistic_question_round_trips():
    result = validate_adaptive_question(
        q(
            selection_mode="multiple",
            allow_other=True,
            options=[
                opt("chest", "胸闷", "胸闷，喘不上气", risk_tags=["chest_discomfort"]),
                opt("sweat", "出冷汗", "出冷汗"),
            ],
        )
    )
    assert [item["id"] for item in result["options"]] == ["chest", "sweat"]
    assert result["options"][0]["risk_tags"] == ["chest_discomfort"]
    assert result["options"][1]["risk_tags"] == []
