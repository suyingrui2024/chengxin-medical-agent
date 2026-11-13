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


def _session_baseline_projection(store, state=None) -> dict:
    """Project a bound profile, or the default profile when no binding exists.

    Archive failures return an empty projection and never block consultation.
    An unbound proxy session retains the default projection; attachment
    permission is independently resolved by _consultation_patient_id.
    """
    try:
        bound = None
        snapshot = getattr(state, "runtime_snapshot", None)
        if isinstance(snapshot, dict):
            candidate = snapshot.get("consultation_patient_id")
            if isinstance(candidate, str) and candidate:
                bound = candidate
        patient = store.patient(bound) if bound else store.ensure_default_patient()
        recent = store.recent_confirmed_measurements(patient["patient_id"])
        return build_baseline_projection(patient, recent)
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
    "_session_baseline_projection",
]
