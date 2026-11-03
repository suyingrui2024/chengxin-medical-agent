"""How much has to be collected before a consultation may close.

Two rules, both about refusing to close early.  An urgent finding needs a
minimum set of facts first, because closing on a single clue sends the
patient away on incomplete information.  And a self-check the patient keeps
suspending is capped, because a loop that never terminates is worse than a
step that is skipped once.

The notice is what the patient is told when the cap clears a step, so a
skipped question is never silently dropped.
"""
from __future__ import annotations


URGENT_SINGLE_CLUE_TRIGGERS = frozenset({"症状持续加重", "症状影响评分较高"})

URGENT_MINIMUM_FACTS = ("severity", "associated_symptoms", "patient_goal")

SELF_CHECK_SUSPEND_CAP = 3

SUSPEND_CAP_NOTICE = "这一步先跳过，回到主流程继续。"


__all__ = [
    "SELF_CHECK_SUSPEND_CAP",
    "SUSPEND_CAP_NOTICE",
    "URGENT_MINIMUM_FACTS",
    "URGENT_SINGLE_CLUE_TRIGGERS",
]
