"""Explicit per-turn research budgets; clinical consultation limits are separate."""

DEFAULT_TIME_BUDGET_SECONDS = 180
MAX_TIME_BUDGET_SECONDS = 1800


def time_budget(value):
    if type(value) is not int or not 1 <= value <= MAX_TIME_BUDGET_SECONDS:
        raise ValueError("执行时限需为 1–1800 秒")
    return value
