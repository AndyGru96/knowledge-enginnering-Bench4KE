"""Compute C2 task tables and method/variant summaries from saved run evidence."""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

from restapi.app.config import METHODS, VARIANTS
from restapi.app.services.ontology_metrics import analyze_graph, analyze_ontology, as_int, as_number, rate, rounded, unavailable_metrics
from restapi.app.utils.ontology_artifacts import as_bool, load_task_graph

def analyze_tasks(task_rows: list[dict[str, str]], base_dir: Path) -> list[dict[str, Any]]:
    """Evaluate indexed original output; failures have unknown denominators."""
    output: list[dict[str, Any]] = []
    for task in task_rows:
        base = {
            "task_id": task["task_id"],
            "dataset_id": task["dataset_id"],
            "method": task["method"],
            "prompt_variant": task["prompt_variant"],
            "parse_success": as_bool(task["parse_success"]),
        }
        if not base["parse_success"]:
            metrics = unavailable_metrics()
            if not as_bool(task["result_available"]):
                metrics["metric_status"] = "metric_unavailable_generation_failed"
                metrics["metric_unavailable_reason"] = task.get("generation_error") or "Generation did not complete"
                if task.get("status") != "completed":
                    metrics["metric_status"] = "metric_unavailable_task_incomplete"
            output.append({**base, **metrics})
            continue
        graph, error = load_task_graph(task, base_dir)
        if graph is None:
            raise ValueError(f"Recorded parse_success disagrees with response documents: {error}")
        output.append({**base, **analyze_graph(graph)})
    return output


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selections: list[tuple[str, str, list[dict[str, Any]]]] = []
    for method in METHODS:
        if not any(row["method"] == method for row in rows):
            continue
        for variant in VARIANTS:
            if not any(row["method"] == method and row["prompt_variant"] == variant for row in rows):
                continue
            selections.append(
                (
                    method,
                    variant,
                    [
                        row
                        for row in rows
                        if row["method"] == method
                        and row["prompt_variant"] == variant
                    ],
                )
            )
        selections.append(
            (method, "ALL", [row for row in rows if row["method"] == method])
        )
    selections.append(("TOTAL", "ALL", rows))

    def sum_field(selected: list[dict[str, Any]], field: str) -> int:
        return sum(
            as_int(row.get(field))
            for row in selected
            if row.get(field) not in (None, "")
        )

    output: list[dict[str, Any]] = []
    for method, variant, selected in selections:
        available = [row for row in selected if row["metric_status"] in {"available", "empty_ontology", "no_evaluable_entities"}]
        class_count = sum_field(available, "class_count")
        object_count = sum_field(available, "object_property_count")
        datatype_count = sum_field(available, "datatype_property_count")
        assessed = sum_field(available, "assessed_entity_count")
        documentation_literals = sum_field(available, "documentation_literal_count")
        ontology_metadata_tasks = sum(
            as_bool(row["ontology_metadata_presence"]) for row in available
        )
        output.append(
            {
                "method": method,
                "prompt_variant": variant,
                "tasks_total": len(selected),
                "empty_ontology_tasks": sum(as_bool(row.get("empty_ontology")) for row in available),
                "tasks_with_evaluable_entities": sum(as_bool(row.get("has_evaluable_entities")) for row in available),
                "metric_available_tasks": len(available),
                "metric_unavailable_tasks": len(selected) - len(available),
                "metric_availability_rate": rounded(rate(len(available), len(selected))),
                "class_entities": class_count,
                "class_labels": sum_field(available, "class_label_count"),
                "class_label_coverage": rounded(rate(sum_field(available, "class_label_count"), class_count)),
                "class_comments_definitions": sum_field(available, "class_comment_definition_count"),
                "class_comment_definition_coverage": rounded(rate(sum_field(available, "class_comment_definition_count"), class_count)),
                "object_property_entities": object_count,
                "object_property_labels": sum_field(available, "object_property_label_count"),
                "object_property_label_coverage": rounded(rate(sum_field(available, "object_property_label_count"), object_count)),
                "object_property_comments_definitions": sum_field(available, "object_property_comment_definition_count"),
                "object_property_comment_definition_coverage": rounded(rate(sum_field(available, "object_property_comment_definition_count"), object_count)),
                "datatype_property_entities": datatype_count,
                "datatype_property_labels": sum_field(available, "datatype_property_label_count"),
                "datatype_property_label_coverage": rounded(rate(sum_field(available, "datatype_property_label_count"), datatype_count)),
                "datatype_property_comments_definitions": sum_field(available, "datatype_property_comment_definition_count"),
                "datatype_property_comment_definition_coverage": rounded(rate(sum_field(available, "datatype_property_comment_definition_count"), datatype_count)),
                "ontology_metadata_present_tasks": ontology_metadata_tasks,
                "ontology_metadata_rate_available_tasks": rounded(rate(ontology_metadata_tasks, len(available))),
                "ontology_metadata_observed_lower_bound_all_tasks": rounded(rate(ontology_metadata_tasks, len(selected))),
                "documentation_literal_count": documentation_literals,
                "documentation_total_characters": sum_field(available, "documentation_total_characters"),
                "average_documentation_length": rounded(rate(sum_field(available, "documentation_total_characters"), documentation_literals)),
                "assessed_entities": assessed,
                "nontrivial_documented_entities": sum_field(available, "nontrivial_comment_definition_entity_count"),
                "nontrivial_comment_definition_rate": rounded(rate(sum_field(available, "nontrivial_comment_definition_entity_count"), assessed)),
                "mean_triples_parseable_tasks": rounded(mean(as_number(row["triples_count"]) for row in available) if available else ""),
                "mean_classes_parseable_tasks": rounded(mean(as_number(row["class_count"]) for row in available) if available else ""),
                "denominator_policy": (
                    "entity coverage uses known entities in parseable ontologies; "
                    "every unparseable task remains an explicit metric-unavailable task "
                    "in the task denominator"
                ),
            }
        )
    return output


def main() -> int:
    import argparse
    import json
    from restapi.app.routers.ontology_benchmark import validate_run
    from restapi.app.utils.ontology_artifacts import write_csv

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    _, tasks = validate_run(args.run_dir.resolve(), args.allow_partial)
    rows = analyze_tasks(tasks, args.run_dir.resolve())
    write_csv(args.output_dir / "c2_documentation_completeness.csv", rows)
    write_csv(args.output_dir / "c2_documentation_summary.csv", summarize(rows))
    print(json.dumps({"tasks": len(rows), "output_dir": str(args.output_dir.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
