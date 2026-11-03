"""Which patient a session is about, and the baseline shown for them.

A consultation can be about somebody other than the person using the app, so
a session resolves to an archive owner before anything may be attached to it.
The projection built from that owner is what the frontend shows as the
patient's recent baseline.

Every function here fails open: an archive that cannot be read yields an
empty projection rather than an error, because a missing baseline must not
stop a consultation from starting.
"""
from __future__ import annotations

from ..guards.baseline_projection import build_baseline_projection


def _default_baseline_projection(store) -> dict:
    """Build the P2c projection for the default (self) profile.

    Fail-open by contract: archive errors must never break session
    creation, so any failure yields an empty projection.
    """
    try:
        patient = store.ensure_default_patient()
        recent = store.recent_confirmed_measurements(patient["patient_id"])
        return build_baseline_projection(patient, recent)
    except Exception:
        return {}

def _session_baseline_projection(store, state=None) -> dict:
    """Build the P2c projection for a session's bound subject (P2d).

    A session bound via POST /api/sessions/{id}/bind-patient projects
    the bound member; an unbound session keeps the old semantics — the
    default (self) profile. Only an explicit snapshot binding counts:
    an unbound proxy session (consult_for != self, no binding) still
    gets the default projection at creation, exactly as before, and
    never an empty one via the attach-gate resolver. Fail-open like
    _default_baseline_projection.
    """
    try:
        bound = None
        snapshot = getattr(state, "runtime_snapshot", None)
        if isinstance(snapshot, dict):
            candidate = snapshot.get("consultation_patient_id")
            if isinstance(candidate, str) and candidate:
                bound = candidate
        if bound is not None:
            patient = store.patient(bound)
            recent = store.recent_confirmed_measurements(patient["patient_id"])
            return build_baseline_projection(patient, recent)
        return _default_baseline_projection(store)
    except Exception:
        return {}

def _consultation_patient_id(state, default_patient_id):
    """Resolve the archive owner for an attach gate (P2d).

    A session bound to a member (runtime_snapshot key written by a
    future member picker) resolves to that member; an unbound session
    keeps the old semantics — self consultations use the default
    profile, proxy consultations without a binding resolve to nothing
    and therefore cannot attach (same rejection as before, with a
    message that names the missing binding instead of demanding self).
    """
    snapshot = state.runtime_snapshot
    if isinstance(snapshot, dict):
        bound = snapshot.get("consultation_patient_id")
        if isinstance(bound, str) and bound:
            return bound
    if state.get("consult_for") == "self":
        return default_patient_id
    return None


__all__ = [
    "_consultation_patient_id",
    "_default_baseline_projection",
    "_session_baseline_projection",
]
