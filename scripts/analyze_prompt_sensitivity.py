"""Recompute the parse-success and ontology-term prompt-sensitivity analyses."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
import numpy as np
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD
from scipy import stats


from restapi.app.config import METHODS, ROOT, VARIANTS
from restapi.app.routers.ontology_benchmark import validate_run
from scripts.analyze_documentation_completeness import analyze_tasks, summarize
from restapi.app.utils.ontology_artifacts import (DEFAULT_BASE, as_bool, load_task_graph, parse_turtle, read_csv, write_csv, write_json)

_POLICY = yaml.safe_load((ROOT / "config/c3_analysis_policy.yaml").read_text(encoding="utf-8"))
BOOTSTRAP_SEED = int(_POLICY["term_endpoint"]["bootstrap_seed"])
BOOTSTRAP_RESAMPLES = int(_POLICY["term_endpoint"]["bootstrap_resamples"])
EXACT_DISCORDANT_THRESHOLD = int(_POLICY["binary_endpoint"]["exact_below_discordant"])

COMPARISONS = (("P0", "P1"), ("P0", "P2"), ("P1", "P2"))
BUILT_INS = tuple(str(namespace) for namespace in (RDF, RDFS, OWL, XSD))
DECLARED_TYPES = {
    OWL.Class,
    RDFS.Class,
    OWL.ObjectProperty,
    OWL.DatatypeProperty,
    OWL.AnnotationProperty,
    RDF.Property,
    OWL.NamedIndividual,
}
RELATION_PREDICATES = {
    RDFS.domain,
    RDFS.range,
    RDFS.subClassOf,
    OWL.equivalentClass,
    OWL.disjointWith,
    OWL.inverseOf,
}


def ontology_terms(path: Path, base: str = DEFAULT_BASE) -> tuple[set[str] | None, str | None]:
    graph, error = parse_turtle(path.read_bytes().decode("utf-8"), base)
    return (graph_terms(graph), None) if graph is not None else (None, error)


def graph_terms(graph: Graph) -> set[str]:
    ontology_iris = {
        subject
        for subject in graph.subjects(RDF.type, OWL.Ontology)
        if isinstance(subject, URIRef)
    }
    terms: set[URIRef] = set()
    for subject, object_type in graph.subject_objects(RDF.type):
        if object_type in DECLARED_TYPES and isinstance(subject, URIRef):
            terms.add(subject)
    for subject, predicate, object_value in graph:
        if predicate in RELATION_PREDICATES:
            if isinstance(subject, URIRef):
                terms.add(subject)
            if isinstance(object_value, URIRef):
                terms.add(object_value)
    return {
            str(term)
            for term in terms
            if term not in ontology_iris and not str(term).startswith(BUILT_INS)
        }


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def build_term_jaccard_rows(
    index_rows: list[dict[str, str]], base_dir: Path
) -> list[dict[str, Any]]:
    """Build all selected pairwise term overlaps; flag empty sets separately."""
    index = {
        (row["method"], row["dataset_id"], row["prompt_variant"]): row
        for row in index_rows
    }
    output: list[dict[str, Any]] = []
    for method in METHODS:
        datasets = sorted(
            {
                dataset
                for candidate, dataset, _variant in index
                if candidate == method
            }
        )
        for dataset in datasets:
            extracted: dict[str, set[str] | None] = {}
            errors: dict[str, str] = {}
            for variant in VARIANTS:
                row = index.get((method, dataset, variant))
                if (
                    row is None
                    or not as_bool(row.get("result_available", True))
                    or not as_bool(row.get("parse_success", False))
                ):
                    extracted[variant] = None
                    errors[variant] = "not_parseable_or_unavailable"
                    continue
                graph, error = load_task_graph(row, base_dir)
                extracted[variant] = graph_terms(graph) if graph is not None else None
                errors[variant] = error or ("no_evaluable_terms" if not extracted[variant] else "")
            p0_p1 = (
                jaccard(extracted["P0"], extracted["P1"])
                if extracted["P0"] is not None and extracted["P1"] is not None
                else None
            )
            p0_p2 = (
                jaccard(extracted["P0"], extracted["P2"])
                if extracted["P0"] is not None and extracted["P2"] is not None
                else None
            )
            p1_p2 = jaccard(extracted["P1"], extracted["P2"]) if extracted["P1"] is not None and extracted["P2"] is not None else None
            output.append(
                {
                    "method": method,
                    "dataset_id": dataset,
                    "J_P1_P2": p1_p2,
                    "P0_has_evaluable_terms": bool(extracted["P0"]),
                    "P1_has_evaluable_terms": bool(extracted["P1"]),
                    "P2_has_evaluable_terms": bool(extracted["P2"]),
                    "J_P0_P1_status": "available" if extracted["P0"] and extracted["P1"] else "unavailable_or_empty_terms",
                    "J_P0_P2_status": "available" if extracted["P0"] and extracted["P2"] else "unavailable_or_empty_terms",
                    "J_P1_P2_status": "available" if extracted["P1"] and extracted["P2"] else "unavailable_or_empty_terms",
                    "P0_term_count": len(extracted["P0"]) if extracted["P0"] is not None else None,
                    "P1_term_count": len(extracted["P1"]) if extracted["P1"] is not None else None,
                    "P2_term_count": len(extracted["P2"]) if extracted["P2"] is not None else None,
                    "J_P0_P1": p0_p1,
                    "J_P0_P2": p0_p2,
                    "paired_difference_P0P2_minus_P0P1": p0_p2 - p0_p1 if p0_p1 is not None and p0_p2 is not None else None,
                    "P0_extraction_error": errors["P0"],
                    "P1_exclusion_or_error": errors["P1"],
                    "P2_exclusion_or_error": errors["P2"],
                    "included_in_wilcoxon": all(bool(extracted[v]) for v in VARIANTS),
                }
            )
    return output


def load_panel(path: Path) -> dict[str, dict[str, dict[str, int | None]]]:
    panel: dict[str, dict[str, dict[str, int | None]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in read_csv(path):
        value = (
            int(as_bool(row["parse_success"]))
            if as_bool(row["result_available"])
            else None
        )
        panel[row["method"]][row["dataset_id"]][row["prompt_variant"]] = value
    return panel


def complete_triplets(
    method_rows: dict[str, dict[str, int | None]],
) -> list[list[int]]:
    return [
        [int(values[variant]) for variant in VARIANTS]
        for values in method_rows.values()
        if all(values.get(variant) is not None for variant in VARIANTS)
    ]


def cochran_q(values: list[list[int]]) -> dict[str, Any]:
    paired_n = len(values)
    if paired_n < 2:
        return {
            "paired_n": paired_n,
            "statistic": None,
            "p_value": None,
            "status": "not_estimable_insufficient_complete_pairs",
        }
    array = np.asarray(values, dtype=float)
    column_totals = array.sum(axis=0)
    row_totals = array.sum(axis=1)
    denominator = len(VARIANTS) * array.sum() - np.square(row_totals).sum()
    if denominator <= 0:
        return {
            "paired_n": paired_n,
            "statistic": None,
            "p_value": None,
            "status": "not_estimable_no_within_pair_variation",
        }
    statistic = (len(VARIANTS) - 1) * (
        len(VARIANTS) * np.square(column_totals).sum() - array.sum() ** 2
    ) / denominator
    return {
        "paired_n": paired_n,
        "statistic": float(statistic),
        "p_value": float(stats.chi2.sf(statistic, len(VARIANTS) - 1)),
        "status": "estimated",
    }


def holm_adjust(p_values: list[float | None]) -> list[float | None]:
    indices = sorted(
        (index for index, value in enumerate(p_values) if value is not None),
        key=lambda index: float(p_values[index]),
    )
    adjusted: list[float | None] = [None] * len(p_values)
    running = 0.0
    for rank, index in enumerate(indices):
        candidate = min(1.0, (len(indices) - rank) * float(p_values[index]))
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


def mcnemar_rows(
    method: str,
    method_rows: dict[str, dict[str, int | None]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for left, right in COMPARISONS:
        if not any(left in values for values in method_rows.values()) or not any(right in values for values in method_rows.values()):
            continue
        pairs = [
            (values.get(left), values.get(right))
            for values in method_rows.values()
            if values.get(left) is not None and values.get(right) is not None
        ]
        left_only = sum(a == 1 and b == 0 for a, b in pairs)
        right_only = sum(a == 0 and b == 1 for a, b in pairs)
        discordant = left_only + right_only
        row: dict[str, Any] = {
            "method": method,
            "comparison": f"{left}_vs_{right}",
            "paired_n": len(pairs),
            "left_success_right_failure": left_only,
            "left_failure_right_success": right_only,
            "test": None,
            "statistic": None,
            "p_value_raw": None,
            "p_value_holm": None,
            "risk_difference_right_minus_left": None,
            "matched_odds_ratio_right_over_left": None,
            "status": "not_estimable_no_pairs",
        }
        if pairs and discordant:
            if discordant < EXACT_DISCORDANT_THRESHOLD:
                row["test"] = "exact_binomial_two_sided"
                row["p_value_raw"] = float(
                    stats.binomtest(min(left_only, right_only), discordant, 0.5).pvalue
                )
            else:
                row["test"] = "continuity_corrected_asymptotic"
                statistic = (abs(left_only - right_only) - 1) ** 2 / discordant
                row["statistic"] = statistic
                row["p_value_raw"] = float(stats.chi2.sf(statistic, 1))
            row["risk_difference_right_minus_left"] = (right_only - left_only) / len(pairs)
            if left_only == 0 and right_only > 0:
                row["matched_odds_ratio_right_over_left"] = "infinite"
            elif right_only == 0 and left_only > 0:
                row["matched_odds_ratio_right_over_left"] = 0.0
            else:
                row["matched_odds_ratio_right_over_left"] = right_only / left_only
            row["status"] = "estimated"
        elif pairs and discordant == 0:
            row.update(
                {
                    "test": "exact_no_discordance",
                    "p_value_raw": 1.0,
                    "risk_difference_right_minus_left": 0.0,
                    "matched_odds_ratio_right_over_left": "undefined_0_over_0",
                    "status": "estimated",
                }
            )
        output.append(row)
    for row, adjusted in zip(output, holm_adjust([row["p_value_raw"] for row in output])):
        row["p_value_holm"] = adjusted
    return output


def wilcoxon_rows(term_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method in METHODS:
        method_rows = [row for row in term_rows if row["method"] == method]
        if not method_rows:
            continue
        usable = [
            row
            for row in method_rows
            if as_bool(row["included_in_wilcoxon"])
            and str(row["J_P0_P1"]).strip()
            and str(row["J_P0_P2"]).strip()
        ]
        result: dict[str, Any] = {
            "method": method,
            "paired_n": len(usable),
            "excluded_pairs": len(method_rows) - len(usable),
            "zero_differences": None,
            "positive_rank_sum": None,
            "negative_rank_sum": None,
            "statistic": None,
            "p_value": None,
            "median_difference_P0P2_minus_P0P1": None,
            "matched_rank_biserial": None,
            "bootstrap_ci_low": None,
            "bootstrap_ci_high": None,
            "implementation": f"scipy.stats.wilcoxon asymptotic Pratt; paired bootstrap {BOOTSTRAP_RESAMPLES} seed {BOOTSTRAP_SEED}",
            "status": "not_estimable_no_parseable_complete_pairs",
        }
        if all(row.get("P0_term_count") is None or row.get("P1_term_count") is None or row.get("P2_term_count") is None for row in method_rows):
            result["status"] = "not_estimable_requires_P0_P1_P2"
        if usable:
            left = np.asarray([float(row["J_P0_P1"]) for row in usable])
            right = np.asarray([float(row["J_P0_P2"]) for row in usable])
            differences = right - left
            ranks = stats.rankdata(np.abs(differences))
            positive = float(ranks[differences > 0].sum())
            negative = float(ranks[differences < 0].sum())
            denominator = positive + negative
            result.update(
                {
                    "zero_differences": int(np.sum(differences == 0)),
                    "positive_rank_sum": positive,
                    "negative_rank_sum": negative,
                    "median_difference_P0P2_minus_P0P1": float(np.median(differences)),
                    "matched_rank_biserial": (positive - negative) / denominator if denominator else 0.0,
                }
            )
            if np.all(differences == 0):
                result.update({"statistic": 0.0, "p_value": 1.0, "status": "estimated_all_zero"})
            else:
                test = stats.wilcoxon(
                    right,
                    left,
                    zero_method="pratt",
                    alternative="two-sided",
                    method="approx",
                )
                result.update({"statistic": float(test.statistic), "p_value": float(test.pvalue), "status": "estimated"})
            generator = np.random.default_rng(BOOTSTRAP_SEED)
            samples = np.median(
                differences[generator.integers(0, len(differences), size=(BOOTSTRAP_RESAMPLES, len(differences)))],
                axis=1,
            )
            result["bootstrap_ci_low"], result["bootstrap_ci_high"] = [
                float(value) for value in np.quantile(samples, [0.025, 0.975])
            ]
        output.append(result)
    return output


def effect_size_rows(
    mcnemar: list[dict[str, Any]], wilcoxon: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    output = [
        {
            "endpoint": "parse_success",
            "method": row["method"],
            "comparison": row["comparison"],
            "paired_n": row["paired_n"],
            "effect_name": "paired_risk_difference_and_matched_odds_ratio",
            "effect_value": row["risk_difference_right_minus_left"],
            "secondary_effect_value": row["matched_odds_ratio_right_over_left"],
            "status": row["status"],
        }
        for row in mcnemar
    ]
    output.extend(
        {
            "endpoint": "P0_centred_ontology_term_jaccard",
            "method": row["method"],
            "comparison": "J_P0_P1_vs_J_P0_P2",
            "paired_n": row["paired_n"],
            "effect_name": "matched_rank_biserial",
            "effect_value": row["matched_rank_biserial"],
            "secondary_effect_value": None,
            "status": row["status"],
        }
        for row in wilcoxon
    )
    return output


def analyze(
    panel_path: Path,
    term_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    panel = load_panel(panel_path)
    cochran_rows: list[dict[str, Any]] = []
    mcnemar: list[dict[str, Any]] = []
    for method in METHODS:
        if method not in panel:
            continue
        triplets = complete_triplets(panel[method])
        q_result = cochran_q(triplets)
        if not all(any(v in values for values in panel[method].values()) for v in VARIANTS):
            q_result["status"] = "not_estimable_three_variants_not_selected"
        cochran_rows.append(
            {
                "method": method,
                "variants": ";".join(VARIANTS),
                "paired_n": q_result["paired_n"],
                "excluded_pairs": len(panel[method]) - q_result["paired_n"],
                "statistic": q_result["statistic"],
                "degrees_of_freedom": 2 if q_result["statistic"] is not None else None,
                "p_value": q_result["p_value"],
                "status": q_result["status"],
            }
        )
        mcnemar.extend(mcnemar_rows(method, panel[method]))
    wilcoxon = wilcoxon_rows(term_rows)
    return {
        "cochran": cochran_rows,
        "mcnemar": mcnemar,
        "wilcoxon": wilcoxon,
        "effects": effect_size_rows(mcnemar, wilcoxon),
    }


def write_results(analysis: dict[str, list[dict[str, Any]]], output_dir: Path) -> None:
    names = {
        "cochran": "c3_cochran_q_results.csv",
        "mcnemar": "c3_mcnemar_results.csv",
        "wilcoxon": "c3_wilcoxon_results.csv",
        "effects": "c3_effect_sizes.csv",
    }
    for key, name in names.items():
        if analysis[key]:
            write_csv(output_dir / name, analysis[key])


def analyze_run(run_dir: Path, output_dir: Path, *, allow_partial: bool = False) -> dict:
    manifest, rows = validate_run(run_dir, allow_partial)
    index_path = run_dir / "task_index.csv"
    detail = analyze_tasks(rows, run_dir)
    terms = build_term_jaccard_rows(rows, run_dir)
    write_csv(output_dir / "c2_documentation_completeness.csv", detail)
    write_csv(output_dir / "c2_documentation_summary.csv", summarize(detail))
    if terms:
        write_csv(output_dir / "c3_term_jaccard.csv", terms)
    write_results(analyze(index_path, term_rows=terms), output_dir)
    summary = {
        "run_id": manifest["run_id"], "tasks": len(rows),
        "run_status": manifest["status"],
        "unfinished_tasks": sum(r["status"] != "completed" for r in rows),
        "empty_ontology_tasks": sum(as_bool(r["empty_ontology"]) for r in rows),
        "tasks_with_evaluable_entities": sum(as_bool(r["has_evaluable_entities"]) for r in rows),
        "generation_complete_tasks": sum(r["result_available"].lower() == "true" for r in rows),
        "parseable_tasks": sum(r["parse_success"].lower() == "true" for r in rows),
        "source": "Original response documents, independently parsed; whole-response Turtle endpoint",
    }
    write_json(output_dir / "analysis_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true", help="Explicitly include an unfinished run with missing-task markers")
    args = parser.parse_args()
    print(json.dumps(analyze_run(args.run_dir.resolve(), args.output_dir.resolve(), allow_partial=args.allow_partial), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
