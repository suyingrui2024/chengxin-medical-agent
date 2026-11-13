"""Controlled subagent coordinator for the adaptive consultation engine.

Implements docs/features/联网与子智能体工具设计.md §5/§6 (Phase C, core C1):
a Python-layer coordinator that lets the adaptive engine spawn bounded,
read-only subagents for a closed set of task kinds. The medical DSH
bridge stays zero-tool by design; sub-runs go through the same
``decide_cancellable`` backend abstraction as the foreground, receive a
one-shot projected view of the case (never the CaseState itself), and
their reports only become consumable after a deterministic validation
pipeline (verbatim evidence checks, banned-phrase lint, uncertainty
floor, drop-rate fuse, staleness watermark).

Fail-open throughout: any failure degrades to a status dict (or ``None``
/ ``[]`` for the convenience runners) instead of raising into the
consultation loop. Subagent output is advisory data with provenance; it
never writes facts and never reaches the patient unmediated.

Only the five names below are the package's surface. When the coordinator
was split into lifecycle/branch/validate, this file kept the whole import
block of the module it replaced -- 52 bound names, 47 of them read by
nobody -- so `from medagent.subagents import Thread, json` both worked and
lied about what the package is.
"""

from __future__ import annotations

from .constants import KIND_INSTRUCTIONS, KINDS
from .content import _content_index, watermark
from .coordinator import SubagentCoordinator

__all__ = [
    "KINDS",
    "KIND_INSTRUCTIONS",
    "SubagentCoordinator",
    "_content_index",
    "watermark",
]
