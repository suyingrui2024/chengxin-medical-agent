import csv
def read_rows(stream):
    return list(csv.DictReader(stream))
