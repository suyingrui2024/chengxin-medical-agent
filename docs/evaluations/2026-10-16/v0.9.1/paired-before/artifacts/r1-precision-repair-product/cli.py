"""stdin/stdout bridge for exact unit conversion.

Reads ``{"value": "...", "from": "...", "to": "..."}`` from stdin and writes
``{"result": "<exact decimal string>"}`` to stdout.  The exact string produced
by :func:`units.convert` is passed through untouched: no float round-trip and
no fixed number of decimal places.
"""

import json
import sys

from units import convert


def main(argv=None):
    try:
        data = json.load(sys.stdin)
        result = convert(data["value"], data["from"], data["to"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
        return 2
    sys.stdout.write(json.dumps({"result": result}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
