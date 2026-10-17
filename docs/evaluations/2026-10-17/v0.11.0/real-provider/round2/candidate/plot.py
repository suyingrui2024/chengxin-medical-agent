#!/usr/bin/env python3
"""Synthetic (non-medical) unit-conversion demo.

Computes g = kg * 1000 with Decimal, writes analysis.csv, and saves a
bar chart analysis.png (>= 400x240 px) using the same computed data.
Labels are English to avoid missing CJK fonts. No clinical output.
"""
import csv
from decimal import Decimal

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SAMPLES = [("A", "1.25"), ("B", "2.00"), ("C", "0.75")]
KG_TO_G = Decimal(1000)


def build_rows():
    rows = []
    for name, kg_text in SAMPLES:
        kg = Decimal(kg_text)
        g = (kg * KG_TO_G).quantize(Decimal("1"))
        rows.append((name, kg, g))
    return rows


def write_csv(rows, path="analysis.csv"):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["sample", "kg", "g"])
        for name, kg, g in rows:
            writer.writerow([name, str(kg), str(g)])


def write_png(rows, path="analysis.png"):
    labels = [name for name, _, _ in rows]
    values = [float(g) for _, _, g in rows]
    fig, ax = plt.subplots(figsize=(4.0, 2.4), dpi=100)
    ax.bar(labels, values, color="#3b6ea5")
    ax.set_xlabel("Sample")
    ax.set_ylabel("Mass (g)")
    ax.set_title("Mass by sample")
    for x, v in zip(labels, values):
        ax.text(x, v, f"{v:g}", ha="center", va="bottom", fontsize=8)
    ax.set_ylim(0, max(values) * 1.2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    rows = build_rows()
    write_csv(rows)
    write_png(rows)
    for name, kg, g in rows:
        print(f"{name}: {kg} kg -> {g} g")
    print("wrote analysis.csv and analysis.png")


if __name__ == "__main__":
    main()
