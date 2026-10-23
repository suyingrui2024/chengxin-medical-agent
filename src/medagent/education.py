"""Post-conclusion web enrichment: patient reading + guideline telemetry.

Design: docs/联网与子智能体工具设计.md §4.3/§4.5/§7. This module is an
advisory layer *outside* the consultation kernel. It runs after a case is
closed with a finished conclusion sheet and may only:

- append a ``further_reading`` block (authoritative citations, T1/T2 in
  v1) to the sheet — asynchronously, fail-open, revision-bound; and/or
- emit guideline-freshness / conflict telemetry for the clinical
  review queue (review channel — never writes back to the case).

Retrieved content never feeds triage, disposition, directions or the
safety net. Queries on the patient path are built by web_tools from
controlled vocabulary only; the review path goes through the scrub
gate. Event payloads carry structured metadata only — never fetched
content and never patient text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import BoundedSemaphore, Lock, Thread
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .domain import Phase
from .events import Event


MAX_CONCURRENT_ENRICHMENTS = 2
ENRICH_DEADLINE_SECONDS = 25.0
MAX_EDUCATION_SEARCHES = 3
MAX_EDUCATION_FETCHES = 2
MAX_CITATIONS = 4
MAX_RESULTS_PER_SEARCH = 5
FETCH_MAX_CHARS = 8000
PATIENT_VISIBLE_TIERS = ("T1", "T2")
GUIDELINE_STALE_AFTER = timedelta(days=365 * 5)

# A citation quote or snippet carrying a dosing pattern never reaches
# the patient area (design §4.3): web text must not smuggle concrete
# medication instructions past the curated content.
DOSE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mg|毫克|ml|片|粒|次\s*/\s*日)"
    r"|每日\s*\d+\s*次"
    r"|\d+\s*次\s*/\s*日"
)

# Conservative conflict heuristic: phrasing that contradicts the local
# safety net ("return_now_if"). A hit is never shown to the patient;
# it is logged for the clinical review queue instead (design §4.5).
CONFLICT_PHRASES = (
    "无需就医",
    "不需要就医",
    "不用就医",
    "不必就医",
    "无需看医生",
    "不用看医生",
    "不用去医院",
    "不必去医院",
    "在家观察即可",
    "在家休息即可",
    # English equivalents (matched case-insensitively via _conflict_in).
    "no need to see a doctor",
    "no need to seek medical",
    "does not need medical attention",
    "no need to go to the hospital",
    "can be monitored at home",
    "can be watched at home",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[。；;！!？?])|\n+")


def _contains_dose(text: Optional[str]) -> bool:
    return bool(text) and bool(DOSE_PATTERN.search(text))


def _conflict_in(text: Optional[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(
        phrase in text or phrase in lowered for phrase in CONFLICT_PHRASES
    )


def _parse_published(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _pick_quote(text: Optional[str], keywords: Sequence[str]) -> Optional[str]:
    """A verbatim fragment of *text* (<=120 chars), preferring the first
    sentence that mentions one of the direction keywords. Slicing keeps
    the fragment a true substring of the source."""
    if not text:
        return None
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    if not parts:
        return None
    chosen = parts[0]
    for part in parts:
        if any(keyword and keyword in part for keyword in keywords):
            chosen = part
            break
    quote = chosen[:120].strip()
    return quote or None


@dataclass
class _EnrichJob:
    session_id: str
    engine: Any
    sheet_revision: int
    directions: List[Tuple[str, str]]  # (hypothesis_id, patient_label)
    care_topics: List[str]
    return_now_if: List[str]
    forbidden_fragments: List[str]
    created_at: float = field(default_factory=monotonic)


class EducationEnricher:
    """Bounded, revision-bound post-conclusion web enrichment.

    Mirrors BackgroundReviewCoordinator's shape (daemon threads, a
    bounded semaphore, fail-open everywhere) but serves the web
    advisory layer: the education channel writes ``further_reading``
    onto the finished sheet; the review channel is telemetry only.
    """

    def __init__(self, events, web_tools=None, run_inline: bool = False):
        self.events = events
        self._injected_tools = web_tools
        self._run_inline = run_inline
        self._slots = BoundedSemaphore(MAX_CONCURRENT_ENRICHMENTS)
        self._lock = Lock()
        self._inflight: set = set()
        self._closed = False

    # -- web_tools access (lazy: the module is a sibling deliverable) --

    def _module(self):
        from . import web_tools

        return web_tools

    def _tools(self):
        if self._injected_tools is not None:
            return self._injected_tools
        return self._module().WebTools(events=self.events)

    def _mode(self) -> str:
        tools = self._injected_tools
        probe = getattr(tools, "web_mode", None)
        if callable(probe):
            return probe()
        return self._module().web_mode()

    def _sources_version(self):
        tools = self._injected_tools
        value = getattr(tools, "sources_version", None)
        if value is None:
            value = getattr(self._module(), "sources_version", None)
        return value() if callable(value) else value

    def _make_citation(self, item: Dict[str, Any], quote: str) -> Dict[str, Any]:
        tools = self._injected_tools
        factory = getattr(tools, "make_citation", None)
        if factory is None:
            factory = self._module().make_citation
        return factory(item, quote)

    # -- scheduling --

    def schedule(self, engine, state) -> bool:
        """Queue enrichment for a finished sheet. Returns True when a
        run was started. All gating happens here, in the caller's
        thread; the worker receives only plain snapshot data."""
        try:
            mode = self._mode()
        except Exception:
            return False
        if mode == "off":
            return False
        if getattr(state, "phase", None) != Phase.CLOSED:
            return False
        conclusion = getattr(state, "conclusion", None) or {}
        if not conclusion:
            return False
        if not conclusion.get("directions_visible"):
            return False
        raw_directions = conclusion.get("directions") or []
        directions = [
            (entry.get("hypothesis_id"), entry.get("patient_label"))
            for entry in raw_directions
            if isinstance(entry, dict) and entry.get("patient_label")
        ]
        if not directions:
            return False
        revision = conclusion.get("revision")
        existing = conclusion.get("further_reading") or {}
        if revision is not None and existing.get("sheet_revision") == revision:
            return False
        key = (state.session_id, revision)
        with self._lock:
            if self._closed or key in self._inflight:
                return False
            self._inflight.add(key)

        self_care = conclusion.get("self_care") or {}
        care_topics = [
            text
            for group in ("do", "avoid", "measure")
            for text in (self_care.get(group) or [])
            if isinstance(text, str)
        ]
        fragments: List[str] = []
        chief = state.facts.get("chief_complaint")
        if chief is not None and chief.value:
            fragments.append(str(chief.value))
        for fact in state.facts.values():
            if fact.evidence:
                fragments.append(fact.evidence)
        job = _EnrichJob(
            session_id=state.session_id,
            engine=engine,
            sheet_revision=revision,
            directions=directions,
            care_topics=care_topics,
            return_now_if=list(conclusion.get("return_now_if") or []),
            forbidden_fragments=fragments,
        )
        if mode in ("education", "all"):
            self._start(job, self._run_education, "medical-education")
        if mode in ("review", "all"):
            self._start(job, self._run_review, "medical-education-review")
        return True

    def _start(self, job: _EnrichJob, target, name: str) -> None:
        if self._run_inline:
            target(job)
            return
        Thread(target=target, args=(job,), name=name, daemon=True).start()

    def _release(self, job: _EnrichJob) -> None:
        with self._lock:
            self._inflight.discard((job.session_id, job.sheet_revision))

    def close(self) -> None:
        with self._lock:
            self._closed = True

    # -- education channel (patient-visible, writes further_reading) --

    def _run_education(self, job: _EnrichJob) -> None:
        if not self._slots.acquire(blocking=False):
            self._release(job)
            return
        try:
            self._enrich(job)
        finally:
            self._slots.release()
            self._release(job)

    def _enrich(self, job: _EnrichJob) -> None:
        tools = self._tools()
        started = job.created_at
        citations: List[Dict[str, Any]] = []
        errors: List[str] = []
        searches = 0
        fetches = 0
        try:
            for hypothesis_id, label in job.directions:
                if len(citations) >= MAX_CITATIONS:
                    break
                if searches >= MAX_EDUCATION_SEARCHES:
                    break
                if monotonic() - started > ENRICH_DEADLINE_SECONDS:
                    break
                try:
                    query = tools.build_education_query(label)
                    result = tools.search(
                        query,
                        purpose="patient_education",
                        min_tier="T3",
                        max_results=MAX_RESULTS_PER_SEARCH,
                        caller="education",
                    )
                except Exception as exc:
                    errors.append(type(exc).__name__)
                    continue
                searches += 1
                status = result.get("status")
                items = result.get("results") or []
                if status not in (None, "ok") and not items:
                    errors.append(str(status))
                    continue
                candidates = self._candidates(job, items, hypothesis_id)
                for item in candidates:
                    if len(citations) >= MAX_CITATIONS:
                        break
                    quote = None
                    if fetches < MAX_EDUCATION_FETCHES and (
                        monotonic() - started <= ENRICH_DEADLINE_SECONDS
                    ):
                        quote, fetches = self._quote_via_fetch(
                            job, tools, item, label, fetches, hypothesis_id
                        )
                        if quote is False:  # fetched and rejected
                            continue
                    if quote is None:
                        quote = _pick_quote(
                            item.get("snippet"), [label] + job.care_topics
                        )
                    if not quote or _contains_dose(quote):
                        continue
                    citations.append(self._make_citation(item, quote))
        except Exception as exc:  # fail-open: never disturb the case
            self.events.append(
                Event(
                    job.session_id,
                    "web.education_failed",
                    {"error": type(exc).__name__},
                )
            )
            return
        if not citations:
            if errors:
                self.events.append(
                    Event(
                        job.session_id,
                        "web.education_failed",
                        {"error": errors[0]},
                    )
                )
            return
        self._write_back(job, citations, started)

    def _candidates(
        self, job: _EnrichJob, items: List[Dict[str, Any]], hypothesis_id: str
    ) -> List[Dict[str, Any]]:
        """Patient-visible filter: T1/T2 only (D3), published-date rule,
        conflict screen on the snippet, dose screen on the snippet.
        Survivors are ordered tier-first, then most recent."""
        now = datetime.now(timezone.utc)
        kept = []
        for index, item in enumerate(items):
            if item.get("tier") not in PATIENT_VISIBLE_TIERS:
                continue
            snippet = item.get("snippet")
            if _conflict_in(snippet):
                self.events.append(
                    Event(
                        job.session_id,
                        "web.conflict_detected",
                        {
                            "url": item.get("url"),
                            "hypothesis_id": hypothesis_id,
                            "local_content_ref": "return_now_if",
                        },
                    )
                )
                continue
            if _contains_dose(snippet):
                continue
            published = _parse_published(item.get("published_at"))
            if published is not None and now - published > GUIDELINE_STALE_AFTER:
                continue  # too old to hand to a patient without review
            kept.append((item, published, index))
        kept.sort(
            key=lambda entry: (
                0 if entry[0].get("tier") == "T1" else 1,
                entry[1] is None,
                -(entry[1].timestamp() if entry[1] else 0.0),
                entry[2],
            )
        )
        return [item for item, _published, _index in kept]

    def _quote_via_fetch(
        self,
        job: _EnrichJob,
        tools,
        item: Dict[str, Any],
        label: str,
        fetches: int,
        hypothesis_id: str,
    ):
        """Fetch one candidate's full text. Returns (quote, fetches):
        quote is a verbatim string, None to fall back to the snippet,
        or False when the fetched page must be rejected outright."""
        url = item.get("url")
        if not url:
            return None, fetches
        try:
            fetched = tools.fetch(url, max_chars=FETCH_MAX_CHARS)
        except Exception:
            return None, fetches  # fetch failure: snippet may still serve
        fetches += 1
        if fetched.get("injection_suspect"):
            return False, fetches
        content = fetched.get("content")
        if _conflict_in(content):
            self.events.append(
                Event(
                    job.session_id,
                    "web.conflict_detected",
                    {
                        "url": url,
                        "hypothesis_id": hypothesis_id,
                        "local_content_ref": "return_now_if",
                    },
                )
            )
            return False, fetches
        quote = _pick_quote(content, [label] + job.care_topics)
        if quote and _contains_dose(quote):
            # The page's relevant passage pushes dosing: drop the item
            # entirely rather than falling back to its snippet.
            return False, fetches
        if quote:
            return quote, fetches
        return None, fetches

    def _write_back(
        self, job: _EnrichJob, citations: List[Dict[str, Any]], started: float
    ) -> None:
        try:
            state = job.engine.state(job.session_id)
        except KeyError:
            return
        with job.engine._lock_for(job.session_id):
            conclusion = state.conclusion or {}
            if conclusion.get("revision") != job.sheet_revision:
                self.events.append(
                    Event(
                        job.session_id,
                        "web.education_stale",
                        {"sheet_revision": job.sheet_revision},
                    )
                )
                return
            conclusion["further_reading"] = {
                "status": "ready",
                "sheet_revision": job.sheet_revision,
                "sources_version": self._sources_version(),
                "citations": citations,
            }
            state.conclusion = conclusion
            for citation in citations:
                self.events.append(
                    Event(
                        job.session_id,
                        "web.citation_attached",
                        {
                            "citation_id": citation.get("citation_id"),
                            "sheet_revision": job.sheet_revision,
                        },
                    )
                )
            self.events.append(
                Event(
                    job.session_id,
                    "web.education_completed",
                    {
                        "citation_count": len(citations),
                        "duration_ms": max(
                            0, round((monotonic() - started) * 1000)
                        ),
                    },
                )
            )

    # -- review channel (telemetry only, never writes back) --

    def _run_review(self, job: _EnrichJob) -> None:
        try:
            tools = self._tools()
            now = datetime.now(timezone.utc)
            for hypothesis_id, label in job.directions:
                try:
                    query = tools.scrub_free_query(
                        f"{label} 诊疗指南",
                        forbidden_fragments=job.forbidden_fragments,
                    )
                    if query is None:
                        self.events.append(
                            Event(
                                job.session_id,
                                "web.blocked",
                                {
                                    "caller": "review",
                                    "purpose": "guideline_check",
                                    "hypothesis_id": hypothesis_id,
                                    "reason": "query_blocked",
                                },
                            )
                        )
                        continue
                    result = tools.search(
                        query,
                        purpose="guideline_check",
                        min_tier="T1",
                        max_results=MAX_RESULTS_PER_SEARCH,
                        caller="review",
                    )
                except Exception:
                    continue  # telemetry must never disturb anything
                for item in result.get("results") or []:
                    if item.get("tier") != "T1":
                        continue
                    published = _parse_published(item.get("published_at"))
                    if published is not None and (
                        now - published > GUIDELINE_STALE_AFTER
                    ):
                        self.events.append(
                            Event(
                                job.session_id,
                                "web.guideline_stale",
                                {
                                    "hypothesis_id": hypothesis_id,
                                    "url": item.get("url"),
                                    "published_at": item.get("published_at"),
                                },
                            )
                        )
                    if _conflict_in(item.get("snippet")):
                        self.events.append(
                            Event(
                                job.session_id,
                                "web.conflict_detected",
                                {
                                    "url": item.get("url"),
                                    "hypothesis_id": hypothesis_id,
                                    "local_content_ref": "return_now_if",
                                    "caller": "review",
                                },
                            )
                        )
        finally:
            self._release(job)
