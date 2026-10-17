"""Command line interface for :mod:`units`.

Reads a single JSON object from stdin, e.g.
``{"value": "1234", "from": "mg", "to": "g"}`` and writes exactly
``{"result":"1.234"}`` to stdout.  The conversion result is kept as the exact
decimal string produced by :func:`units.convert`; it is never round-tripped
through ``float`` and never truncated to a fixed number of decimals.
"""

import json
import sys

from units import convert


def main():
    data = json.load(sys.stdin)
    result = convert(data["value"], data["from"], data["to"])
    # ``separators`` keeps the output byte-for-byte identical to the contract
    # ``{"result":"1.234"}`` (no spaces).
    print(json.dumps({"result": result}, separators=(",", ":")))


if __name__ == "__main__":
    main()
