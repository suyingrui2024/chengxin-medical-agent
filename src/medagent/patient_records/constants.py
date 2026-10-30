from __future__ import annotations
import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4
from ..extensions.glucose import convert_glucose
from ..measurement_kinds import (
    KNOWN_KINDS,
    MeasurementKindError,
    kind_spec,
    normalize_measurement,
    trend_summary,
)
from ..medical_images import InspectedImage, validate_recognition

SCHEMA_VERSION = 2


BASELINE_FIELDS = (
    "birth_year",
    "sex",
    "height_cm",
    "weight_kg",
    "chronic_conditions",
    "allergies",
    "medications",
    "surgical_history",
    "birth_year_month",
)


BASELINE_LIST_LIMIT = 20


BASELINE_STRING_LIMIT = 200


_ALLERGY_KINDS = ("drug", "food", "other")


MEASUREMENT_CONTEXTS = {
    "fasting",
    "before_meal",
    "after_meal_1h",
    "after_meal_2h",
    "bedtime",
    "random",
    "unknown",
}


GENERIC_MEASUREMENT_CONTEXTS = {"random", "unknown"}


GLUCOSE_MEASUREMENT_CONTEXTS = MEASUREMENT_CONTEXTS


_MEASUREMENT_CONTEXT_LABELS = {
    "fasting": "空腹",
    "before_meal": "餐前",
    "after_meal_1h": "餐后 1 小时",
    "after_meal_2h": "餐后 2 小时",
    "bedtime": "睡前",
    "random": "随机",
    "unknown": "场景暂不确定",
}
