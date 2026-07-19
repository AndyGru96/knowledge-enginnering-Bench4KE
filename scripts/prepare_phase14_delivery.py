"""Create deterministic Phase 14 figures, figure manifest, and report tables."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from scripts.summarize_c3_term_jaccard import summarize as summarize_term_jaccard


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"
TABLES = REPORTS / "final_tables.md"
FIGURE_MANIFEST = FIGURES / "figure_manifest.json"

PARSE_CSV = RESULTS / "final_method_variant_parse_summary.csv"
COST_CSV = RESULTS / "final_generation_cost_summary.csv"
REPAIR_CSV = RESULTS / "final_repair_length_summary.csv"
C2_SUMMARY_CSV = RESULTS / "c2_documentation_summary.csv"
C2_C3_CSV = RESULTS / "final_c2_c3_summary.csv"
C3_MANIFEST = RESULTS / "c3_repair_aware_analysis_manifest.json"
TERM_JACCARD_CSV = RESULTS / "c3_repair_aware_term_jaccard.csv"

METHODS = ("ontogenia", "domain-ontogen", "neon-gpt")
METHOD_LABELS = {
    "ontogenia": "Ontogenia",
    "domain-ontogen": "Domain-OntoGen",
    "neon-gpt": "NeOn-GPT",
}
METHOD_SHORT_LABELS = {
    "ontogenia": "Ontogenia",
    "domain-ontogen": "Domain",
    "neon-gpt": "NeOn-GPT",
}
VARIANTS = ("P0", "P1", "P2")
VARIANT_COLORS = {"P0": "#4C78A8", "P1": "#F58518", "P2": "#54A24B"}
SERIES_COLORS = {"raw": "#6B7280", "normalized": "#E69F00", "final": "#009E73"}

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 9.5,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
    }
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(value: str | float) -> float:
    return float(value) * 100.0


def fmt_pct(value: str | float) -> str:
    return f"{pct(value):.1f}%"


def save_figure(fig: plt.Figure, filename: str, title: str, sources: list[Path]) -> dict[str, Any]:
    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / filename
    fig.savefig(
        path,
        dpi=180,
        bbox_inches="tight",
        pad_inches=0.12,
        metadata={"Software": "Bench4KE Phase 14 deterministic matplotlib"},
    )
    plt.close(fig)
    with Image.open(path) as image:
        width, height = image.size
        image.verify()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256(path),
        "width_px": width,
        "height_px": height,
        "title": title,
        "source_files": [source.relative_to(ROOT).as_posix() for source in sources],
    }


def parse_success_figure(parse_rows: list[dict[str, str]]) -> dict[str, Any]:
    rows = [row for row in parse_rows if row["method"] != "TOTAL"]
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    x = np.arange(len(METHODS))
    width = 0.23
    for index, variant in enumerate(VARIANTS):
        selected = [next(row for row in rows if row["method"] == method and row["prompt_variant"] == variant) for method in METHODS]
        values = [pct(row["final_parse_rate"]) for row in selected]
        bars = ax.bar(x + (index - 1) * width, values, width, label=variant, color=VARIANT_COLORS[variant])
        for bar, row in zip(bars, selected):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.8, f"{row['final_parse_success']}/17", ha="center", va="bottom", fontsize=8)
    ax.set_title("Final parse success varies more by method than prompt variant")
    ax.set_ylabel("Final parse success (%)")
    ax.set_xticks(x, [METHOD_LABELS[method] for method in METHODS])
    ax.set_ylim(0, 110)
    ax.grid(axis="y", color="#D1D5DB", linewidth=0.7, alpha=0.7)
    ax.legend(title="Variant", frameon=False, ncol=3, loc="upper left")
    fig.tight_layout()
    return save_figure(fig, "parse_success_by_method_variant.png", ax.get_title(), [PARSE_CSV])


def parse_stage_figure(parse_rows: list[dict[str, str]]) -> dict[str, Any]:
    rows = [row for row in parse_rows if row["method"] != "TOTAL"]
    labels = [f"{METHOD_SHORT_LABELS[row['method']]}\n{row['prompt_variant']}" for row in rows]
    x = np.arange(len(rows))
    width = 0.24
    fig, ax = plt.subplots(figsize=(11.2, 5.0))
    series = (
        ("raw", "Raw", "raw_parse_rate"),
        ("normalized", "Normalized", "normalized_parse_rate"),
        ("final", "Final", "final_parse_rate"),
    )
    for index, (key, label, field) in enumerate(series):
        values = [pct(row[field]) for row in rows]
        ax.bar(x + (index - 1) * width, values, width, label=label, color=SERIES_COLORS[key])
    ax.set_title("Normalization and repair recover many parseable outputs")
    ax.set_ylabel("Parse success (%)")
    ax.set_xticks(x, labels, rotation=0)
    ax.set_ylim(0, 105)
    ax.grid(axis="y", color="#D1D5DB", linewidth=0.7, alpha=0.7)
    ax.legend(frameon=False, ncol=3, loc="upper left")
    fig.tight_layout()
    return save_figure(fig, "raw_normalized_final_parse.png", ax.get_title(), [PARSE_CSV])


def repair_length_figure(repair_rows: list[dict[str, str]]) -> dict[str, Any]:
    rows = [row for row in repair_rows if row["method"] != "TOTAL"]
    labels = [f"{METHOD_SHORT_LABELS[row['method']]}\n{row['prompt_variant']}" for row in rows]
    x = np.arange(len(rows))
    width = 0.34
    fig, ax = plt.subplots(figsize=(11.2, 5.0))
    repairs = [int(row["repair_calls"]) for row in rows]
    lengths = [int(row["length_terminations"]) for row in rows]
    repair_bars = ax.bar(x - width / 2, repairs, width, label="Repair calls", color="#7C3AED")
    length_bars = ax.bar(x + width / 2, lengths, width, label="Length terminations", color="#DC2626")
    ax.bar_label(repair_bars, padding=2, fontsize=8)
    ax.bar_label(length_bars, padding=2, fontsize=8)
    ax.set_title("Repairs are concentrated in NeOn-GPT; length stops affect multiple pipelines")
    ax.set_ylabel("Internal calls")
    ax.set_xticks(x, labels)
    ax.set_ylim(0, max(repairs + lengths) + 6)
    ax.grid(axis="y", color="#D1D5DB", linewidth=0.7, alpha=0.7)
    ax.legend(frameon=False, ncol=2, loc="upper left")
    fig.tight_layout()
    return save_figure(fig, "repair_length_summary.png", ax.get_title(), [REPAIR_CSV])


def c2_coverage_figure(c2_rows: list[dict[str, str]]) -> dict[str, Any]:
    total = next(row for row in c2_rows if row["method"] == "TOTAL" and row["prompt_variant"] == "ALL")
    entity_labels = ("Classes", "Object properties", "Datatype properties")
    label_values = [
        pct(total["class_label_coverage"]),
        pct(total["object_property_label_coverage"]),
        pct(total["datatype_property_label_coverage"]),
    ]
    documentation_values = [
        pct(total["class_comment_definition_coverage"]),
        pct(total["object_property_comment_definition_coverage"]),
        pct(total["datatype_property_comment_definition_coverage"]),
    ]
    x = np.arange(len(entity_labels))
    width = 0.34
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    label_bars = ax.bar(x - width / 2, label_values, width, label="Label coverage", color="#2563EB")
    documentation_bars = ax.bar(x + width / 2, documentation_values, width, label="Comment/definition coverage", color="#0D9488")
    ax.bar_label(label_bars, labels=[f"{value:.1f}%" for value in label_values], padding=2, fontsize=8)
    ax.bar_label(documentation_bars, labels=[f"{value:.1f}%" for value in documentation_values], padding=2, fontsize=8)
    ax.set_title("C2 documentation coverage among parseable final ontologies")
    ax.set_ylabel("Micro coverage (%)")
    ax.set_xticks(x, entity_labels)
    ax.set_ylim(0, 105)
    ax.grid(axis="y", color="#D1D5DB", linewidth=0.7, alpha=0.7)
    ax.legend(frameon=False, ncol=2, loc="upper left")
    fig.text(0.5, 0.01, "Metric availability: 101/153 tasks (66.0%); 52 unparseable outputs remain explicitly unavailable.", ha="center", fontsize=8.5, color="#374151")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    return save_figure(fig, "c2_documentation_coverage.png", ax.get_title(), [C2_SUMMARY_CSV])


def c3_summary_figure(c3_rows: list[dict[str, str]]) -> dict[str, Any]:
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    x = np.arange(len(c3_rows))
    width = 0.34
    cochran = [float(row["c3_cochran_p_value"]) for row in c3_rows]
    wilcoxon = [float(row["c3_wilcoxon_p_value"]) if row["c3_wilcoxon_p_value"] else math.nan for row in c3_rows]
    cochran_bars = ax.bar(x - width / 2, cochran, width, label="Cochran Q", color="#4C78A8")
    wilcoxon_bars = ax.bar(x + width / 2, wilcoxon, width, label="Term-Jaccard Wilcoxon", color="#B279A2")
    ax.bar_label(cochran_bars, labels=[f"p={value:.3f}" for value in cochran], padding=2, fontsize=8)
    for bar, value in zip(wilcoxon_bars, wilcoxon):
        if math.isnan(value):
            ax.text(bar.get_x() + bar.get_width() / 2, 0.02, "NA", ha="center", va="bottom", fontsize=8, color="#6B7280")
        else:
            ax.text(bar.get_x() + bar.get_width() / 2, value + 0.02, f"p={value:.3f}", ha="center", va="bottom", fontsize=8)
    ax.axhline(0.05, color="#DC2626", linestyle="--", linewidth=1.1, label="α = 0.05")
    ax.set_title("C3 inferential tests: no significant parse-success or P1–P2 drift-magnitude difference")
    ax.set_ylabel("p-value")
    ax.set_xticks(x, [METHOD_LABELS[row["method"]] for row in c3_rows])
    ax.set_ylim(0, 1.03)
    ax.grid(axis="y", color="#D1D5DB", linewidth=0.7, alpha=0.7)
    ax.legend(frameon=False, ncol=3, loc="upper right")
    fig.tight_layout()
    return save_figure(fig, "c3_prompt_sensitivity_summary.png", ax.get_title(), [C2_C3_CSV, C3_MANIFEST])


def build_tables(
    parse_rows: list[dict[str, str]],
    cost_rows: list[dict[str, str]],
    c2_rows: list[dict[str, str]],
    c3_rows: list[dict[str, str]],
    term_summary: dict[str, Any],
) -> None:
    parse_detail = [row for row in parse_rows if row["method"] != "TOTAL"]
    cost_detail = [row for row in cost_rows if row["method"] != "TOTAL"]
    c2_detail = [row for row in c2_rows if row["method"] != "TOTAL" and row["prompt_variant"] in VARIANTS]
    c2_total = next(row for row in c2_rows if row["method"] == "TOTAL")
    total_cost = next(row for row in cost_rows if row["method"] == "TOTAL")
    lines = [
        "# Final Report Tables",
        "",
        "All tables are deterministic derivatives of admitted A1 P0 and repair-aware-admitted A2 P1/P2 evidence. Unavailable values are shown as `NA`, not zero-filled.",
        "",
        "## A1/A2 parse summary",
        "",
        "| Method | Variant | Tasks | Raw parse | Normalized parse | Final parse |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in parse_detail:
        lines.append(
            f"| {METHOD_LABELS[row['method']]} | {row['prompt_variant']} | {row['tasks']} | "
            f"{row['raw_parse_success']}/{row['tasks']} ({fmt_pct(row['raw_parse_rate'])}) | "
            f"{row['normalized_parse_success']}/{row['tasks']} ({fmt_pct(row['normalized_parse_rate'])}) | "
            f"{row['final_parse_success']}/{row['tasks']} ({fmt_pct(row['final_parse_rate'])}) |"
        )
    lines.extend(
        [
            "",
            "## C2 documentation summary",
            "",
            f"Metric availability is {c2_total['metric_available_tasks']}/{c2_total['tasks_total']} ({fmt_pct(c2_total['metric_availability_rate'])}); {c2_total['metric_unavailable_tasks']} unparseable tasks remain explicitly unavailable.",
            "",
            "| Method | Variant | Available | Class label | Class docs | Object label | Object docs | Datatype label | Datatype docs |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in c2_detail:
        lines.append(
            f"| {METHOD_LABELS[row['method']]} | {row['prompt_variant']} | {row['metric_available_tasks']}/{row['tasks_total']} | "
            f"{fmt_pct(row['class_label_coverage'])} | {fmt_pct(row['class_comment_definition_coverage'])} | "
            f"{fmt_pct(row['object_property_label_coverage'])} | {fmt_pct(row['object_property_comment_definition_coverage'])} | "
            f"{fmt_pct(row['datatype_property_label_coverage'])} | {fmt_pct(row['datatype_property_comment_definition_coverage'])} |"
        )
    lines.extend(
        [
            "",
            "## C3 statistical summary",
            "",
            "| Method | Binary triplets | Cochran Q p | Term pairs | Wilcoxon p | Interpretation |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in c3_rows:
        wilcoxon = f"{float(row['c3_wilcoxon_p_value']):.4f}" if row["c3_wilcoxon_p_value"] else "NA"
        interpretation = (
            "No significant parse-success difference; P1-vs-P2 drift comparison not estimable"
            if row["method"] == "ontogenia"
            else "No significant parse-success difference; P1-vs-P2 drift magnitude not statistically distinguishable"
        )
        lines.append(
            f"| {METHOD_LABELS[row['method']]} | {row['c3_binary_paired_n']} | {float(row['c3_cochran_p_value']):.4f} | "
            f"{row['c3_term_paired_n']} | {wilcoxon} | {interpretation} |"
        )
    lines.extend(
        [
            "",
            "## C3 term-Jaccard descriptive summary",
            "",
            "| Method | Comparison | Eligible n | Median | Min | Max |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for method in METHODS:
        method_summary = term_summary["methods"][method]
        for comparison, label in (("J_P0_P1", "J(P0,P1)"), ("J_P0_P2", "J(P0,P2)")):
            values = method_summary["comparisons"][comparison]
            formatted = ["NA" if values[key] is None else f"{float(values[key]):.4f}" for key in ("median", "min", "max")]
            lines.append(
                f"| {METHOD_LABELS[method]} | {label} | {values['eligible_n']} | "
                f"{formatted[0]} | {formatted[1]} | {formatted[2]} |"
            )
    lines.extend(
        [
            "",
            "These descriptive comparisons show term-set overlap with P0; they are not tests of invariance. The Wilcoxon test compares paired P1-versus-P2 drift magnitudes from P0 and uses complete-pair counts of 0 for Ontogenia, 16 for Domain-OntoGen, and 6 for NeOn-GPT. The NeOn-GPT Wilcoxon result has a small paired sample and an asymptotic Pratt approximation, so it has limited power and should be treated cautiously.",
        ]
    )
    lines.extend(
        [
            "",
            "## Cost, runtime, and token summary",
            "",
            "| Method | Variant | Calls | Prompt tokens | Completion tokens | Total tokens | Wall-clock sum (s) |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in cost_detail:
        lines.append(
            f"| {METHOD_LABELS[row['method']]} | {row['prompt_variant']} | {row['internal_calls']} | "
            f"{int(row['prompt_tokens']):,} | {int(row['completion_tokens']):,} | {int(row['total_tokens']):,} | "
            f"{float(row['task_wall_clock_seconds']):,.3f} |"
        )
    lines.append(
        f"| **Total** | **All** | **{total_cost['internal_calls']}** | **{int(total_cost['prompt_tokens']):,}** | "
        f"**{int(total_cost['completion_tokens']):,}** | **{int(total_cost['total_tokens']):,}** | "
        f"**{float(total_cost['task_wall_clock_seconds']):,.3f}** |"
    )
    lines.extend(
        [
            "",
            "## Blocker and admission history",
            "",
            "| Item | Historical/strict status | Downstream disposition |",
            "|---|---|---|",
            "| Stage A1 | Schema-v1 identity: **FAIL** | Immutable evidence **ADMITTED** through schema-v2 semantic-equivalence audit |",
            "| Stage A2 | Historical strict schema-v2 identity: **FAIL** | Historical failure preserved |",
            "| Phase 12 A2 sidecar | **PARTIALLY ADMITTED** | 75/102 tasks under the stricter all-call suffix criterion |",
            "| Phase 12B A2 sidecar | **ADMITTED** | 102/102 tasks under the repair-aware governance ruling |",
            "| B10 | **RESOLVED** | Resolved by repair-aware sidecar governance, not by rewriting envelopes |",
            "| B6 | **OPEN / non-blocking** | Monitored Ontogenia model-output quality risk |",
            "",
            "The original Stage A2 execution failed its strict schema-v2 identity contract. A stricter sidecar audit partially admitted the evidence. Under the repair-aware governance ruling, the immutable A2 evidence was admitted for downstream C3 analysis.",
            "",
        ]
    )
    TABLES.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parse_rows = read_csv(PARSE_CSV)
    cost_rows = read_csv(COST_CSV)
    repair_rows = read_csv(REPAIR_CSV)
    c2_rows = read_csv(C2_SUMMARY_CSV)
    c3_rows = read_csv(C2_C3_CSV)
    term_summary = summarize_term_jaccard(TERM_JACCARD_CSV)
    figures = [
        parse_success_figure(parse_rows),
        parse_stage_figure(parse_rows),
        repair_length_figure(repair_rows),
        c2_coverage_figure(c2_rows),
        c3_summary_figure(c3_rows),
    ]
    build_tables(parse_rows, cost_rows, c2_rows, c3_rows, term_summary)
    source_paths = (PARSE_CSV, COST_CSV, REPAIR_CSV, C2_SUMMARY_CSV, C2_C3_CSV, C3_MANIFEST, TERM_JACCARD_CSV)
    manifest = {
        "schema_version": "phase14-figure-manifest-v1",
        "phase": "Revised Phase 14",
        "generation_mode": "deterministic existing-evidence visualization only",
        "model_generation_calls": 0,
        "api_chat_calls": 0,
        "source_files": {path.relative_to(ROOT).as_posix(): sha256(path) for path in source_paths},
        "figures": figures,
        "generator": {
            "path": Path(__file__).relative_to(ROOT).as_posix(),
            "sha256": sha256(Path(__file__)),
            "matplotlib_version": matplotlib.__version__,
            "dpi": 180,
        },
        "report_tables": {
            "path": TABLES.relative_to(ROOT).as_posix(),
            "sha256": sha256(TABLES),
        },
        "claims": {
            "statistically_significant_prompt_superiority": False,
            "historical_stage_a2_strict_status": "FAIL",
            "phase12_strict_sidecar": "PARTIALLY ADMITTED",
            "phase12b_repair_aware_sidecar": "ADMITTED",
        },
    }
    FIGURE_MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"figures": len(figures), "tables": TABLES.relative_to(ROOT).as_posix(), "model_generation_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
