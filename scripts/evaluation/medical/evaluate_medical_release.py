#!/usr/bin/env python3
"""Score clinician-approved release cases without calling a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from medagent.release_evaluation import evaluate_cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate_cases(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
