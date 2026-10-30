"""Controlled subagent coordinator for the adaptive consultation engine.

Implements docs/联网与子智能体工具设计.md §5/§6 (Phase C, core C1):
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
"""

from __future__ import annotations
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib import resources
from threading import BoundedSemaphore, Event as ThreadEvent, Lock, Thread
from time import monotonic
from typing import Any, Dict, List, Optional
from uuid import uuid4
from ..backends import parse_decision
from ..conclusion import BANNED_PHRASES, GAP_IMPACTS, MEASUREMENT_FACTS
from ..decision_wait import DecisionInterrupted
from ..domain import CaseState, utc_now
from ..events import Event
from ..reasoning import ReasoningWorkspace, load_pathways

from .constants import (MODES, TENDENCY_LEVELS, KINDS, _KIND_TOOLS, MAX_BRANCHES, SPAWN_ENVELOPE_TOKENS, SPAWN_ENVELOPE_DEADLINE_S, RECENT_WINDOW_TURNS, MAX_FINDINGS, MAX_CLAIM_CHARS, MAX_CONCLUSION_CHARS, _DISPOSITION_PATTERN, _DOSAGE_PATTERN, _ENVELOPE_HINT, KIND_INSTRUCTIONS, _content_index_cache)
from .content import (watermark, _load_content_json, _content_index, _known_fact_keys, _knowledge_search, _banned_hit)
from .coordinator import (SubagentCoordinator)
from .token_est import (_estimate_roundtrip_tokens)
