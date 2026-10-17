import json, sys
from aggregate import aggregate
from reader import read_rows
if __name__ == "__main__":
    print(json.dumps({"groups": aggregate(read_rows(sys.stdin))}))
