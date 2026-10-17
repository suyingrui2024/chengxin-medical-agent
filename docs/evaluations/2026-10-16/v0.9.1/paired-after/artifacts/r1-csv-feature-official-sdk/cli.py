import json, sys
from reader import read_rows
from aggregate import aggregate
if __name__ == "__main__":
    print(json.dumps({"groups": aggregate(read_rows(sys.stdin))}))
