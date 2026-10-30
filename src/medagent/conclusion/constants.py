from __future__ import annotations
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from importlib import resources
from typing import Any, Dict, List, Optional
from ..baseline_projection import build_allergy_hint
from ..direction_guards import (
    direction_kept,
    is_affirmed_in_text,
    severity_consistent_text,
)
from ..domain import Assertion, CaseState, Phase, RiskLevel
from ..followups import build_follow_up_plan
from ..handoff import LABELS as FACT_LABELS
from ..reasoning import RuleBasedProposer, validate_workspace
from ..text_match import contains_unnegated, find_unnegated_certain
from ..worry import extract_worry_object, is_worry_sentence

RETURN_NOW_IF = [
    "意识异常或抽搐",
    "严重呼吸困难或嘴唇发紫",
    "持续压榨样胸痛",
    "大量或无法控制的出血",
]


BANNED_PHRASES = (
    "确诊",
    "可以排除",
    "没事",
    "不用担心",
    "保证",
    "肯定是",
    "一定是",
    "必须吃药",
)


CERTAINTY_TEMPLATES = {
    "more_likely": "更像是 {label}",
    "possible": "可能是 {label}",
    "cannot_exclude": "目前还不能排除 {label}，这也是建议就诊的原因",
}


ABSTAIN_TEXT = "目前的信息还不足以给出可能的方向——这也是建议你留意变化、必要时线下评估的原因之一"


PATHWAY_GROUPS = {
    "uri": "respiratory",
    "lower_respiratory": "respiratory",
    "asthma_exacerbation": "respiratory",
    "influenza_like": "respiratory",
    "acs_possible": "chest",
    "non_cardiac_chest": "chest",
    "appendicitis": "abdominal",
    "gastroenteritis": "abdominal",
    "gi_bleed": "abdominal",
    "functional_dyspepsia": "abdominal",
    "meningitis_possible": "headache",
    "thunderclap_headache": "headache",
    "migraine_tension": "headache",
    "bppv": "dizziness",
    "posterior_stroke": "dizziness",
    "orthostatic": "dizziness",
    "mechanical_back": "back",
    "radiculopathy": "back",
    "cauda_serious": "back",
    "cystitis": "urinary",
    "pyelonephritis": "urinary",
    "urinary_stone": "urinary",
    "urticaria": "skin",
    "dermatitis": "skin",
    "skin_infection": "skin",
}


PRIOR_RANK = {"high": 3, "medium": 2, "low": 1}


GAP_IMPACTS = {
    "temperature_c": "体温读数会影响对感染程度的判断",
    "severity": "难受程度会影响对紧急程度的判断",
    "trajectory": "变化趋势会影响对病情走向的判断",
    "resp_breathing": "呼吸情况会影响对严重程度的判断",
    "gi_details": "腹痛的部位和性质会影响对腹部急症的判断",
    "head_details": "头痛的发作方式和伴随表现会影响对颅内急症的判断",
    "dizzy_details": "头晕的类型和伴随表现会影响对脑部供血问题的判断",
    "back_details": "腰痛是否影响腿部和大小便会影响对腰椎严重问题的判断",
    "urinary_details": "排尿表现和尿色会影响对尿路感染部位的判断",
    "skin_details": "皮疹的形态和诱因会影响对过敏与感染的判断",
}


DEFAULT_GAP_IMPACT = "这项信息会影响判断"


MEASUREMENT_FACTS = {"temperature_c"}


_cache: Dict[str, Any] = {}


DETAIL_FACT_GROUPS = {
    "resp_cough_details": "respiratory",
    "resp_breathing": "respiratory",
    "gi_details": "abdominal",
    "head_details": "headache",
    "dizzy_details": "dizziness",
    "back_details": "back",
    "urinary_details": "urinary",
    "skin_details": "skin",
}


_ENCODING_QUOTES = frozenset(
    {
        "worsening",
        "improving",
        "stable",
        "intermittent",
        "unknown",
        "present",
        "absent",
        "self",
        "child",
        "other_adult",
    }
)


_AGAINST_LABEL_OVERRIDES = {"resp_breathing": "呼吸困难等表现"}


_REFUSAL_EVIDENCE_MARKERS = ("不想说", "不方便说", "拒绝提供")


_QUALIFIED_MARKERS = ("新出现", "突然出现", "明显加重", "迅速加重", "加重")


_OUTLOOK_QUALIFIER_TOKENS = frozenset({"加重", "变化", "出现", "好转", "缓解"})


_GENERIC_WORD_FRAGMENTS = {"症状", "出现", "感觉", "不适", "情况", "问题"}


_SHORT_TERM_OUTLOOK_MARKERS = (
    "数天内",
    "几天内",
    "1–2 天内",
    "3–5 天",
    "数天",
    # E-09 worsening class (self_care.json course grep, 2026-10-26):
    # respiratory_urgent "数小时内加重" is the E-09 case itself;
    # abdominal_urgent "以小时计" is the other hour-scale template;
    # generic_urgent / headache_urgent / dizziness_urgent
    # "短时间内变化/加重" is the same short-term urgency class.
    # Week-scale templates ("1–2 周内", "一两周内") are NOT added:
    # they are consistent with a chronic course.
    "数小时内",
    "以小时计",
    "短时间内",
)


_CHRONIC_OUTLOOK = (
    "这类不适已持续一段时间，观察期间重点看整体状态是好转还是加重，"
    "出现下方警示信号请及时就医。"
)


_INFANT_OBSERVABLE_DO = "把哭闹、拒食、精神、尿量和排便性状的变化记下来，就诊时告诉医生"


_INFANT_OBSERVABLE_MEASURE = "记录哭闹、精神、小便和大便性状的变化"


_INFANT_WORSENING = "哭闹明显加重、精神变差或尿量明显减少"


_VISIT_GOAL_KEYWORDS = ("检查", "去医院", "就诊", "看医生")


_WORRY_FALLBACK_OBJECT = "你问过的那个担心"


_GOAL_PART_PREFIXES = (
    "我就想知道",
    "我只想知道",
    "最想知道",
    "想知道",
    "想问",
    "要不要",
    "能不能",
    "会不会",
    "需不需要",
    "是不是",
    "是否",
    "平时怎么",
    "吃什么能",
    "吃什么",
    "先吃",
    "还是",
    "还有",
    "需要",
    "先",
    "要",
)


_GOAL_DRUG_TREATMENT_WORDS = (
    "止痛药",
    "退烧药",
    "眼药水",
    "滴眼液",
    "滴眼药",
    "复位",
    "含片",
    "感冒药",
    "抗生素",
    "用药",
    "吃药",
)


_GOAL_PART_FIXED_HANDLING = (
    "这一点这次问诊的信息还不足以直接回答，需当面评估后决定"
)


_CHIEF_CHUNK_STOPWORDS = (
    "我",
    "自己",
    "孩子",
    "咨询",
    "的",
    "了",
    "很",
    "有",
    "是",
    "最近",
    "一直",
    "感觉",
    "出现",
    "情况",
    "问题",
    "这次",
    "有点",
    "比较",
)


_GOAL_VISIT_PART_KEYWORDS = _VISIT_GOAL_KEYWORDS + ("就医",)


UNCERTAINTY_NOTE = (
    "需要说明的是：这次问诊里急症筛查的结果还不明确，"
    "现有信息还不足以完全判断紧急程度，请对照下方加重即就医项留意变化，"
    "必要时尽快线下评估。"
)


_G12_WORSENING_MAP = (
    (("便血", "黑便", "血便"), "再出现便血或黑便", ("便血", "黑便")),
    (("呕血", "吐血"), "再出现呕血", ("呕血",)),
    (("头晕",), "头晕加重", ("头晕",)),
    (("心慌",), "心慌加重", ("心慌",)),
)


ORAL_CHIEF_TERMS = ("牙痛", "牙疼", "牙齿", "牙龈", "口腔")


VISIT_NOTES_CHIEF_MAX_CHARS = 80


VISIT_NOTES_EVIDENCE_MAX = 3


VISIT_NOTES_MAX = 8
