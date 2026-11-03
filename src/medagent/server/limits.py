"""What the HTTP layer accepts.

Two body-size ceilings and the mapping from the slugs the frontend sends to
the measurement kinds the registry knows.  The ceilings live here rather than
in the routes that enforce them because more than one route enforces the
same one, and a limit that is written down in two places is a limit that will
be changed in one of them.
"""
from __future__ import annotations


MAX_BODY_BYTES = 64 * 1024

MAX_IMAGE_BODY_BYTES = 6 * 1024 * 1024

MEASUREMENT_SLUGS = {
    "blood-glucose": "blood_glucose",
    "blood-pressure": "blood_pressure",
    "temperature": "temperature",
    "weight": "weight",
    "blood-oxygen": "blood_oxygen",
}


__all__ = [
    "MAX_BODY_BYTES",
    "MAX_IMAGE_BODY_BYTES",
    "MEASUREMENT_SLUGS",
]
