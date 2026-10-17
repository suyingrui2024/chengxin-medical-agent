#!/usr/bin/env python3
"""Self-check for plot.py artifacts (synthetic, non-medical)."""
import csv
import struct
import sys

EXPECTED_CSV = "sample,kg,g\nA,1.25,1250\nB,2.00,2000\nC,0.75,750\n"


def main():
    with open("analysis.csv", "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    assert raw == EXPECTED_CSV, f"CSV mismatch: {raw!r}"

    with open("analysis.csv", "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["sample", "kg", "g"], rows[0]
    assert [r[0] for r in rows[1:]] == ["A", "B", "C"], rows
    assert len(rows) == 4, rows

    with open("analysis.png", "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG file"
    width, height = struct.unpack(">II", head[16:24])
    assert width >= 400 and height >= 240, f"PNG too small: {width}x{height}"

    print(f"OK csv rows={len(rows)} png={width}x{height}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
