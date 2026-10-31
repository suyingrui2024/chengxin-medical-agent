"""Shared imports, constants and module-level helpers for the package."""
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
from ...backends import parse_decision
from ...conclusion import BANNED_PHRASES, GAP_IMPACTS, MEASUREMENT_FACTS
from ...decision_wait import DecisionInterrupted
from ...domain import CaseState, utc_now
from ...events import Event
from ...reasoning import ReasoningWorkspace, load_pathways

from ..constants import KINDS, KIND_INSTRUCTIONS, MAX_BRANCHES, MAX_CLAIM_CHARS, MAX_CONCLUSION_CHARS, MAX_FINDINGS, MODES, RECENT_WINDOW_TURNS, SPAWN_ENVELOPE_DEADLINE_S, SPAWN_ENVELOPE_TOKENS, TENDENCY_LEVELS, _DISPOSITION_PATTERN, _DOSAGE_PATTERN, _KIND_TOOLS


from ..content import _banned_hit, _content_index, _knowledge_search, _known_fact_keys, watermark
from ..token_est import _estimate_roundtrip_tokens





__all__ = [
    "hashlib",
    "json",
    "os",
    "re",
    "ThreadPoolExecutor",
    "date",
    "resources",
    "BoundedSemaphore",
    "ThreadEvent",
    "Lock",
    "Thread",
    "monotonic",
    "Any",
    "Dict",
    "List",
    "Optional",
    "uuid4",
    "parse_decision",
    "BANNED_PHRASES",
    "GAP_IMPACTS",
    "MEASUREMENT_FACTS",
    "DecisionInterrupted",
    "CaseState",
    "utc_now",
    "Event",
    "ReasoningWorkspace",
    "load_pathways",
    "KINDS",
    "KIND_INSTRUCTIONS",
    "MAX_BRANCHES",
    "MAX_CLAIM_CHARS",
    "MAX_CONCLUSION_CHARS",
    "MAX_FINDINGS",
    "MODES",
    "RECENT_WINDOW_TURNS",
    "SPAWN_ENVELOPE_DEADLINE_S",
    "SPAWN_ENVELOPE_TOKENS",
    "TENDENCY_LEVELS",
    "_DISPOSITION_PATTERN",
    "_DOSAGE_PATTERN",
    "_KIND_TOOLS",
    "_banned_hit",
    "_content_index",
    "_knowledge_search",
    "_known_fact_keys",
    "watermark",
    "_estimate_roundtrip_tokens",
]
