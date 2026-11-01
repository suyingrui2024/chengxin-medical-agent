"""Per-turn context for the consultation pipeline (design doc §4.1).

One ``TurnContext`` is created per submitted user input. It carries the
working text (which location-answer preparation may replace), any prepared
structured location answer, the record-reply reason chosen by the handler
that produced the reply, and the pipeline trace. Triage itself is computed
by ``TriageService`` whose ``scan_text`` is memoized, so every handler in
the turn observes one consistent scan per distinct text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class TurnContext:
    turn_id: str
    text: str
    prepared_location: Optional[Any] = None
    reply_reason: str = ""
    trace: List[str] = field(default_factory=list)
