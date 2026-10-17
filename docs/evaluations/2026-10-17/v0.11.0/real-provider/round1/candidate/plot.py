#!/usr/bin/env python3
"""Synthetic (non-medical) unit conversion demo: kg -> g, CSV + bar chart."""
import csv
from decimal import Decimal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SAMPLES = [("A", "1.25"), ("B", "2.00"), ("C", "0.75")]

rows = []
for name, kg_text in SAMPLES:
    kg = Decimal(kg_text)
    g = kg * Decimal(1000)
    rows.append((name, kg, g))

with open("analysis.csv", "w", newline="", encoding="utf-8") as fh:
    writer = csv.writer(fh)
    writer.writerow(["sample", "kg", "g"])
    for name, kg, g in rows:
        writer.writerow([name, str(kg), str(g)])

labels = [r[0] for r in rows]
values = [float(r[2]) for r in rows]

fig, ax = plt.subplots(figsize=(5, 3), dpi=100)
ax.bar(labels, values, color="#4C72B0")
ax.set_xlabel("Sample")
ax.set_ylabel("Mass (g)")
ax.set_title("Synthetic sample mass (kg converted to g)")
for i, v in enumerate(values):
    ax.text(i, v, f"{v:g}", ha="center", va="bottom")
fig.tight_layout()
fig.savefig("analysis.png")
plt.close(fig)

print("csv rows:", len(rows))
print("values(g):", values)
