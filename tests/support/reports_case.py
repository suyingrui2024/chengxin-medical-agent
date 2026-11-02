"""Conclusion-sheet, report-state and education builders shared by the
handoff, linkage and education suites."""
from medagent.core.domain import Assertion, CaseState


def _fact(key, value, assertion=Assertion.PRESENT):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t1",
        evidence="原话",
    )



def _sheet(
    *,
    directions_visible=True,
    department_visible=True,
    directions_mode=None,
    visit_notes=None,
):
    sheet = {
        "disposition": {
            "level": "routine_visit",
            "text": "可以在家先观察；请在 72 小时内到线下医疗机构就诊评估。",
            "window_hours": 72,
            "department_hint": "呼吸内科",
        },
        "directions": [
            {
                "hypothesis_id": "uri",
                "patient_label": "普通感冒",
                "group": "likely",
                "certainty": "可能",
                "support_quotes": ["患者原话引用"],
                "against_note": "你否认了发热，这一点不支持它",
            }
        ],
        "directions_visible": directions_visible,
        "department_visible": department_visible,
        "self_care": {
            "do": ["多休息"],
            "avoid": ["避免劳累"],
            "measure": ["留意体温变化"],
            "allergy_hint": "你档案里记有青霉素过敏，涉及用药请先核对",
        },
        "return_now_if": ["呼吸困难加重"],
    }
    # v1.3 additive fields: only present when the caller passes them,
    # so legacy sheets (no mode) keep the phase-A shape untouched.
    if directions_mode is not None:
        sheet["directions_mode"] = directions_mode
    if visit_notes is not None:
        sheet["visit_notes"] = visit_notes
    return sheet



def _state(*, sheet=None, baseline=None, follow_up=None):
    state = CaseState(session_id="s1")
    state.record(_fact("chief_complaint", "咳嗽三天"))
    state.record(_fact("sex", "女"))
    state.record(_fact("pregnancy_status", "否"))
    state.record(_fact("onset", None, Assertion.UNKNOWN))
    state.report_items = [
        {
            "name": "血糖",
            "reported_value": "6.1",
            "unit": "mmol/L",
            "reference_range": "",
            "status": "confirmed_by_patient",
            "record_title": "血糖自测",
            "measured_at": "2026-10-24",
            "source": "patient_record",
            "secret_extra": "must-not-leak",
        }
    ]
    if sheet is not None:
        state.conclusion = sheet
    if baseline is not None:
        state.profile_baseline = baseline
    if follow_up is not None:
        state.follow_up = follow_up
    return state



# --- education fixtures (from tests/reports/test_education.py) ---

from datetime import datetime, timedelta, timezone
from threading import RLock
from medagent.core.domain import (
    Assertion,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.reports.education import EducationEnricher


class FakeEvents:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)

    def of_type(self, event_type):
        return [e for e in self.events if e.event_type == event_type]



class FakeWebTools:
    """Stub for web_tools.WebTools with the fixed Phase B API shape."""

    def __init__(self, mode="education", search_handler=None, fetch_handler=None):
        self._mode = mode
        self._search_handler = search_handler
        self._fetch_handler = fetch_handler
        self.search_calls = []
        self.fetch_calls = []
        self._citation_seq = 0

    def web_mode(self):
        return self._mode

    def build_education_query(self, patient_label, topic=None):
        return f"{patient_label} 患者教育"

    def scrub_free_query(self, text, forbidden_fragments=()):
        for fragment in forbidden_fragments:
            if fragment and fragment in text:
                return None
        return text

    def search(self, query, *, purpose, min_tier, max_results, caller):
        self.search_calls.append(
            {
                "query": query,
                "purpose": purpose,
                "min_tier": min_tier,
                "max_results": max_results,
                "caller": caller,
            }
        )
        if self._search_handler is not None:
            return self._search_handler(query, purpose)
        return {"status": "ok", "results": []}

    def fetch(self, url, *, max_chars=8000):
        self.fetch_calls.append(url)
        if self._fetch_handler is not None:
            return self._fetch_handler(url)
        return {
            "content": "",
            "content_sha256": "fake",
            "injection_suspect": False,
            "status": "ok",
        }

    def sources_version(self):
        return "sources-test/v1"

    def make_citation(self, item, quote):
        self._citation_seq += 1
        return {
            "citation_id": f"cit_test_{self._citation_seq}",
            "title": item.get("title"),
            "url": item.get("url"),
            "publisher": "测试机构",
            "published_at": item.get("published_at"),
            "fetched_at": item.get("fetched_at"),
            "tier": item.get("tier"),
            "quote": quote,
        }



class FakeEngine:
    def __init__(self, state):
        self._state = state
        self._lock = RLock()

    def state(self, session_id):
        if session_id != self._state.session_id:
            raise KeyError(session_id)
        return self._state

    def _lock_for(self, session_id):
        if session_id != self._state.session_id:
            raise KeyError(session_id)
        return self._lock



def make_item(tier="T1", *, url="https://www.who.int/a", snippet=None, days_ago=30):
    published = None
    if days_ago is not None:
        published = (
            datetime.now(timezone.utc) - timedelta(days=days_ago)
        ).date().isoformat()
    return {
        "title": "科普条目",
        "url": url,
        "domain": "who.int",
        "snippet": snippet
        if snippet is not None
        else "上呼吸道感染多由病毒引起，通常一周左右自行缓解，多休息多喝水。",
        "published_at": published,
        "fetched_at": "2026-10-23T18:00:00+08:00",
        "tier": tier,
        "source_id": "who.int",
    }



def make_sheet(**overrides):
    sheet = {
        "version": 1,
        "revision": 1,
        "tier": "routine",
        "directions_visible": True,
        "directions": [
            {
                "hypothesis_id": "uri",
                "patient_label": "上呼吸道感染",
                "group": "likely",
                "certainty": "possible",
            }
        ],
        "self_care": {"do": ["多喝水"], "avoid": [], "measure": ["量体温"]},
        "return_now_if": ["呼吸困难", "持续高热不退"],
    }
    sheet.update(overrides)
    return sheet



def make_state(sheet=None, phase=Phase.CLOSED):
    state = CaseState(session_id="sess-edu-1")
    state.phase = phase
    state.closed_reason = "confirmed_by_user"
    state.risk = RiskAssessment(RiskLevel.ROUTINE)
    state.record(
        Fact(
            key="chief_complaint",
            value="咳嗽三天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence="我咳嗽三天了",
        )
    )
    state.conclusion = sheet if sheet is not None else make_sheet()
    return state



def make_enricher(events, tools):
    return EducationEnricher(events, web_tools=tools, run_inline=True)

