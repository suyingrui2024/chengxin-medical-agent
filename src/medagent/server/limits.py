"""What the HTTP layer accepts.

Two body-size ceilings and the mapping from the slugs the frontend sends to
the measurement kinds the registry knows.  The ceilings live here rather than
in the routes that enforce them because more than one route enforces the
same one, and a limit that is written down in two places is a limit that will
be changed in one of them.
"""
from __future__ import annotations


# 研究测试阶段放宽（2026-11-04，F2 复议"不设字数上限"）：消息体不设
# 字数上限，传输层保留 1MB 兜底防荒谬请求——约 33 万汉字，对一次问诊
# 发言等效于无上限，同时图片等其余路由的语义不变。
MAX_BODY_BYTES = 1024 * 1024

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
