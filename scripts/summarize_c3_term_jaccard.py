"""Deterministically summarize available repair-aware C3 term-Jaccard values."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "results/c3_repair_aware_term_jaccard.csv"
METHODS = ("ontogenia", "domain-ontogen", "neon-gpt")
COMPARISONS = ("J_P0_P1", "J_P0_P2")


def _describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"eligible_n": 0, "median": None, "min": None, "max": None}
    return {
        "eligible_n": len(values),
        "median": median(values),
        "min": min(values),
        "max": max(values),
    }


def summarize(path: Path = DEFAULT_INPUT) -> dict[str, Any]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    methods: dict[str, Any] = {}
    for method in METHODS:
        method_rows = [row for row in rows if row["method"] == method]
        comparisons = {
            comparison: _describe(
                [float(row[comparison]) for row in method_rows if row[comparison].strip()]
            )
            for comparison in COMPARISONS
        }
        methods[method] = {
            "dataset_rows": len(method_rows),
            "complete_p1_p2_pairs_in_wilcoxon": sum(
                row["included_in_wilcoxon"].strip().lower() == "true"
                for row in method_rows
            ),
            "comparisons": comparisons,
        }

    return {
        "schema_version": "c3-term-jaccard-descriptive-summary-v1",
        "source": path.relative_to(ROOT).as_posix(),
        "methodology": (
            "Each comparison is summarized independently over non-empty CSV values; "
            "complete_p1_p2_pairs_in_wilcoxon requires both comparisons and follows "
            "the source included_in_wilcoxon flag."
        ),
        "methods": methods,
    }


def main() -> None:
    print(json.dumps(summarize(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
