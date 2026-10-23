"""Explicit per-turn research budgets; clinical consultation limits are separate.

预算口径（执行工作台，本模块只定时限部分）：

- 共享预算池：执行工作台的模型调用、联网（web_search / web_fetch）与
  子智能体派生共用同一个每回合预算池——根智能体与其全部后代共享每回合
  模型调用上限（``max_calls`` 默认 24、合法域 1–64，见 ``execution.py``）
  与本模块的每回合时限（默认 180 秒、上限 1800 秒）。拦截点在
  ``execution-worker.mjs`` 的回环网关（``/model/messages``，先预留计数
  再放行），另有进程终身 200 次模型调用的硬顶。注册能力另有独立预算：
  32 次/回合、256 次/进程终身（同在 worker 内强制）。
- 联网两层上限：搜索 provider 的 ``maxUses`` 为 3（``execution-search.mjs``
  与 ``execution.patch.yml``）；chengxin web 插件另设每会话上限——同一
  会话内 search 与 fetch 合计不超过 30 次，超限抛
  ``WEB_SESSION_BUDGET_EXCEEDED`` 并落 ``web/budget-exceeded`` 事件。
- 子智能体深度上限为 2（``execution.patch.yml`` 中 spawn 与 fork 同），
  后代的模型调用计入同一预算池，不另设额度。
- 临床问诊的限额独立于以上口径、另行规定，不受本模块约束。
"""

DEFAULT_TIME_BUDGET_SECONDS = 180
MAX_TIME_BUDGET_SECONDS = 1800


def time_budget(value):
    if type(value) is not int or not 1 <= value <= MAX_TIME_BUDGET_SECONDS:
        raise ValueError("执行时限需为 1–1800 秒")
    return value
