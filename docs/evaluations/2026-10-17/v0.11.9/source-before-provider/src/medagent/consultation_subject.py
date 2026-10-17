"""Reported relationship hints for one consultation, never verified identity or age."""
from copy import deepcopy
import re


# Attributes are relationship, child descriptor and order. '*' is unspecified.
GROUPS = [
    (("self", "*", "*"), "本人", ("我自己", "我本人", "本人")),
    (("father", "*", "*"), "父亲", ("父亲", "爸爸", "我爸")),
    (("mother", "*", "*"), "母亲", ("母亲", "妈妈", "我妈")),
    (("paternal_grandfather", "*", "*"), "爷爷", ("爷爷", "祖父")),
    (("paternal_grandmother", "*", "*"), "奶奶", ("奶奶", "祖母")),
    (("maternal_grandfather", "*", "*"), "外公", ("外公", "姥爷", "外祖父")),
    (("maternal_grandmother", "*", "*"), "外婆", ("外婆", "姥姥", "外祖母")),
    (("husband", "*", "*"), "丈夫", ("丈夫", "老公")),
    (("wife", "*", "*"), "妻子", ("妻子", "老婆")),
    (("father_in_law", "*", "*"), "公公", ("公公",)),
    (("mother_in_law", "*", "*"), "婆婆", ("婆婆",)),
    (("wife_father", "*", "*"), "岳父", ("岳父", "岳丈")),
    (("wife_mother", "*", "*"), "岳母", ("岳母",)),
    (("older_brother", "*", "*"), "哥哥", ("哥哥",)),
    (("younger_brother", "*", "*"), "弟弟", ("弟弟",)),
    (("older_sister", "*", "*"), "姐姐", ("姐姐",)),
    (("younger_sister", "*", "*"), "妹妹", ("妹妹",)),
    (("child", "son", "eldest"), "大儿子", ("大儿子", "长子")),
    (("child", "son", "youngest"), "小儿子", ("小儿子",)),
    (("child", "daughter", "eldest"), "大女儿", ("大女儿", "长女")),
    (("child", "daughter", "youngest"), "小女儿", ("小女儿",)),
    (("child", "*", "eldest"), "大宝", ("大宝", "老大")),
    (("child", "*", "second"), "二宝", ("二宝", "老二")),
    (("child", "*", "third"), "三宝", ("三宝", "老三")),
    (("child", "son", "*"), "儿子", ("儿子",)),
    (("child", "daughter", "*"), "女儿", ("女儿",)),
    (("child", "*", "*"), "孩子", ("孩子", "宝宝")),
]
LOOKUP = {
    alias: (attrs, label) for attrs, label, aliases in GROUPS for alias in aliases
}
PATTERN = re.compile(
    "|".join(re.escape(v) for v in sorted(LOOKUP, key=len, reverse=True))
)


def compatible(first, second):
    return all(
        a == b or a == "*" or b == "*"
        for a, b in zip(first["attributes"], second["attributes"])
    )


def merge_reference(first, second):
    # Keep an actually reported label while retaining compatible refinements.
    result = deepcopy(
        max([first, second], key=lambda v: sum(a != "*" for a in v["attributes"]))
    )
    result["attributes"] = [
        b if a == "*" else a for a, b in zip(first["attributes"], second["attributes"])
    ]
    return result


def reference(text):
    if not isinstance(text, str):
        return None
    found = []
    for match in PATTERN.finditer(text):
        attrs, label = LOOKUP[match.group()]
        item = {"attributes": list(attrs), "label": label}
        if any(not compatible(item, previous) for previous in found):
            raise ValueError("咨询对象引文包含不同的人，请引用一个明确对象或先澄清。")
        found.append(item)
    if text.strip() in {"我", "自己"}:
        found.append({"attributes": ["self", "*", "*"], "label": "本人"})
    result = None
    for item in found:
        result = merge_reference(result, item) if result else item
    return result


def proposed_profile(category, evidence):
    item = reference(evidence)
    if item and ((item["attributes"][0] == "self") != (category == "self")):
        raise ValueError("咨询对象的本人／代问类别与引文不一致，请核对。")
    if item is None and category == "self":
        item = {"attributes": ["self", "*", "*"], "label": "本人"}
    label = (
        item["label"]
        if item
        else {"self": "本人", "child": "孩子", "other_adult": "其他家人"}.get(category, "待确认对象")
    )
    return {"category": category, "label": label, "reference": item}


def subject_profile(state):
    from .domain import Assertion

    current = state.facts.get("consult_for")
    if current is None or current.assertion != Assertion.PRESENT:
        return None
    selected = None
    for fact in [*state.history, current]:
        if (
            fact.key != "consult_for"
            or fact.assertion != Assertion.PRESENT
            or fact.value != current.value
        ):
            continue
        try:
            profile = proposed_profile(fact.value, fact.evidence)
        except ValueError:
            continue
        item = profile["reference"]
        if selected is None or item and not selected["reference"]:
            selected = {
                **profile,
                "evidence": fact.evidence or "",
                "source_turn_id": fact.turn_id,
            }
        elif item and compatible(selected["reference"], item):
            merged = merge_reference(selected["reference"], item)
            if merged["label"] != selected["label"]:
                selected = {
                    **profile,
                    "evidence": fact.evidence or "",
                    "source_turn_id": fact.turn_id,
                }
            selected["reference"] = merged
    if selected is None:
        selected = {
            **proposed_profile(current.value, None),
            "evidence": current.evidence or "",
            "source_turn_id": current.turn_id,
        }
    return {**deepcopy(selected), "scope": "reported_relationship_only"}


def explicit_switch(text, current):
    """Only explicit present switch phrases; ordinary family history is not a switch."""
    for clause in re.split(r"[，。；！？,;!?\n]", text):
        switch = re.search(r"改问|换成问|换个人|换一个人|(?:另一个|另一位)(?:孩子|儿子|女儿|家人)", clause)
        if switch is None:
            continue
        if re.search(
            r"如果|假如|假设|下次|以后|不是|并非|不想|不要|别|能不能|可不可以|没有说", clause[: switch.start()]
        ):
            continue
        try:
            item = reference(clause[switch.start() :])
        except ValueError:
            continue
        if item is None:
            continue
        forced = switch.group().startswith(("换个人", "换一个人", "另一个", "另一位"))
        prior = current.get("reference") if current else None
        if forced or prior is None or not compatible(prior, item):
            return {
                "category": "self" if item["attributes"][0] == "self" else "proxy",
                "label": ("另一位" + item["label"])
                if switch.group().startswith(("另一个", "另一位"))
                else item["label"],
                "reference": item,
            }
    return None


def conflicting_reference(text, current):
    if not current or not current["reference"] or re.search(r"家族史|家族病史|家族遗传", text):
        return None
    match = PATTERN.search(text)
    if match is None or re.search(
        r"如果|假如|假设|下次|以后|不是|并非|不想|不问|不要|没有说", text[: match.start()]
    ):
        return None
    try:
        item = reference(text)
    except ValueError:
        return None
    if item and not compatible(current["reference"], item):
        return {
            "category": "self" if item["attributes"][0] == "self" else "proxy",
            "label": item["label"],
            "reference": item,
        }
    return None
