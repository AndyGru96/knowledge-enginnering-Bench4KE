"""Measure documentation completeness in generated RDF/OWL ontologies."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DETAIL = ROOT / "results" / "c2_documentation_completeness.csv"
DEFAULT_SUMMARY = ROOT / "results" / "c2_documentation_summary.csv"
METHODS = ("ontogenia", "domain-ontogen", "neon-gpt")
VARIANTS = ("P0", "P1", "P2")
LABEL_PREDICATES = {
    RDFS.label,
    URIRef("http://www.w3.org/2004/02/skos/core#prefLabel"),
}
DOCUMENTATION_PREDICATES = {
    RDFS.comment,
    URIRef("http://www.w3.org/2004/02/skos/core#definition"),
    URIRef("http://purl.org/dc/terms/description"),
    URIRef("http://schema.org/description"),
    URIRef("https://schema.org/description"),
}
NONTRIVIAL_MIN_CHARS = 20
NONTRIVIAL_MIN_WORDS = 3


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        raise ValueError("Cannot write an empty result table")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def as_int(value: Any) -> int:
    return 0 if value in (None, "") else int(float(value))


def as_number(value: Any) -> float:
    return 0.0 if value in (None, "") else float(value)


def rate(numerator: int | float, denominator: int | float) -> float | str:
    return numerator / denominator if denominator else ""


def rounded(value: float | str, digits: int = 9) -> float | str:
    if isinstance(value, float) and math.isfinite(value):
        return round(value, digits)
    return value


def literals_for(graph: Graph, entity: Any, predicates: set[Any]) -> list[str]:
    return [
        str(value).strip()
        for predicate in predicates
        for value in graph.objects(entity, predicate)
        if isinstance(value, Literal) and str(value).strip()
    ]


def is_nontrivial(value: str) -> bool:
    return len(value) >= NONTRIVIAL_MIN_CHARS and len(value.split()) >= NONTRIVIAL_MIN_WORDS


def entity_documentation(graph: Graph, entities: set[Any]) -> dict[str, Any]:
    labels = 0
    documented = 0
    nontrivial = 0
    documentation_literals: list[str] = []
    for entity in entities:
        entity_labels = literals_for(graph, entity, LABEL_PREDICATES)
        entity_docs = literals_for(graph, entity, DOCUMENTATION_PREDICATES)
        labels += bool(entity_labels)
        documented += bool(entity_docs)
        nontrivial += any(is_nontrivial(value) for value in entity_docs)
        documentation_literals.extend(entity_docs)
    return {
        "entities": len(entities),
        "labels": labels,
        "documented": documented,
        "nontrivial": nontrivial,
        "documentation_literals": documentation_literals,
    }


def analyze_graph(graph: Graph) -> dict[str, Any]:
    classes = set(graph.subjects(RDF.type, OWL.Class)) | set(
        graph.subjects(RDF.type, RDFS.Class)
    )
    object_properties = set(graph.subjects(RDF.type, OWL.ObjectProperty))
    datatype_properties = set(graph.subjects(RDF.type, OWL.DatatypeProperty))
    class_docs = entity_documentation(graph, classes)
    object_docs = entity_documentation(graph, object_properties)
    datatype_docs = entity_documentation(graph, datatype_properties)

    ontology_subjects = set(graph.subjects(RDF.type, OWL.Ontology))
    ontology_metadata_statements = sum(
        1
        for subject in ontology_subjects
        for predicate, _value in graph.predicate_objects(subject)
        if predicate != RDF.type
    )
    ontology_docs = [
        value
        for subject in ontology_subjects
        for value in literals_for(graph, subject, DOCUMENTATION_PREDICATES)
    ]
    documentation_literals = (
        class_docs["documentation_literals"]
        + object_docs["documentation_literals"]
        + datatype_docs["documentation_literals"]
        + ontology_docs
    )
    assessed_entities = (
        class_docs["entities"] + object_docs["entities"] + datatype_docs["entities"]
    )
    nontrivial_entities = (
        class_docs["nontrivial"]
        + object_docs["nontrivial"]
        + datatype_docs["nontrivial"]
    )

    return {
        "metric_status": "available",
        "metric_unavailable_reason": "",
        "class_count": class_docs["entities"],
        "class_label_count": class_docs["labels"],
        "class_label_coverage": rounded(rate(class_docs["labels"], class_docs["entities"])),
        "class_comment_definition_count": class_docs["documented"],
        "class_comment_definition_coverage": rounded(
            rate(class_docs["documented"], class_docs["entities"])
        ),
        "object_property_count": object_docs["entities"],
        "object_property_label_count": object_docs["labels"],
        "object_property_label_coverage": rounded(
            rate(object_docs["labels"], object_docs["entities"])
        ),
        "object_property_comment_definition_count": object_docs["documented"],
        "object_property_comment_definition_coverage": rounded(
            rate(object_docs["documented"], object_docs["entities"])
        ),
        "datatype_property_count": datatype_docs["entities"],
        "datatype_property_label_count": datatype_docs["labels"],
        "datatype_property_label_coverage": rounded(
            rate(datatype_docs["labels"], datatype_docs["entities"])
        ),
        "datatype_property_comment_definition_count": datatype_docs["documented"],
        "datatype_property_comment_definition_coverage": rounded(
            rate(datatype_docs["documented"], datatype_docs["entities"])
        ),
        "ontology_declaration_present": bool(ontology_subjects),
        "ontology_metadata_statement_count": ontology_metadata_statements,
        "ontology_metadata_presence": bool(
            ontology_subjects and ontology_metadata_statements
        ),
        "documentation_literal_count": len(documentation_literals),
        "documentation_total_characters": sum(
            len(value) for value in documentation_literals
        ),
        "average_documentation_length": rounded(
            mean(len(value) for value in documentation_literals)
            if documentation_literals
            else 0.0
        ),
        "assessed_entity_count": assessed_entities,
        "nontrivial_comment_definition_entity_count": nontrivial_entities,
        "nontrivial_comment_definition_rate": rounded(
            rate(nontrivial_entities, assessed_entities)
        ),
        "triples_count": len(graph),
        "classes_count_structural": len(classes),
        "object_properties_count_structural": len(object_properties),
        "datatype_properties_count_structural": len(datatype_properties),
    }


def analyze_ontology(path: Path, rdf_format: str = "turtle") -> dict[str, Any]:
    return analyze_graph(Graph().parse(path, format=rdf_format))


def unavailable_metrics() -> dict[str, Any]:
    fields = list(analyze_graph(Graph()))
    row = {field: "" for field in fields}
    row["metric_status"] = "metric_unavailable_unparseable_final_ontology"
    row["metric_unavailable_reason"] = (
        "final_parse_success=false; the unknown entity denominator is not set to zero"
    )
    return row


def analyze_tasks(task_rows: list[dict[str, str]], base_dir: Path) -> list[dict[str, Any]]:
    """Evaluate task rows that point to final ontology files."""
    output: list[dict[str, Any]] = []
    for task in task_rows:
        base = {
            "task_id": task["task_id"],
            "dataset_id": task["dataset_id"],
            "method": task["method"],
            "prompt_variant": task["prompt_variant"],
            "raw_parse_success": as_bool(task["raw_parse_success"]),
            "normalized_parse_success": as_bool(task["normalized_parse_success"]),
            "final_parse_success": as_bool(task["final_parse_success"]),
        }
        if not base["final_parse_success"]:
            output.append({**base, **unavailable_metrics()})
            continue
        ontology_path = Path(task["ontology_path"])
        if not ontology_path.is_absolute():
            ontology_path = base_dir / ontology_path
        output.append({**base, **analyze_ontology(ontology_path)})
    return output


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selections: list[tuple[str, str, list[dict[str, Any]]]] = []
    for method in METHODS:
        for variant in VARIANTS:
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
        available = [row for row in selected if row["metric_status"] == "available"]
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
                "mean_classes_parseable_tasks": rounded(mean(as_number(row["classes_count_structural"]) for row in available) if available else ""),
                "denominator_policy": (
                    "entity coverage uses known entities in parseable final ontologies; "
                    "every unparseable task remains an explicit metric-unavailable task "
                    "in the task denominator"
                ),
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    ontology_parser = subparsers.add_parser("ontology")
    ontology_parser.add_argument("path", type=Path)
    ontology_parser.add_argument("--format", default="turtle")
    summary_parser = subparsers.add_parser("summarize")
    summary_parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    summary_parser.add_argument("--output", type=Path, default=DEFAULT_SUMMARY)
    batch_parser = subparsers.add_parser("batch")
    batch_parser.add_argument("--tasks", type=Path, required=True)
    batch_parser.add_argument("--base-dir", type=Path, default=ROOT)
    batch_parser.add_argument("--detail-output", type=Path, required=True)
    batch_parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "ontology":
        print(json.dumps(analyze_ontology(args.path, args.format), indent=2))
    elif args.command == "summarize":
        write_csv(args.output, summarize(read_csv(args.detail)))
        print(args.output)
    else:
        detail = analyze_tasks(read_csv(args.tasks), args.base_dir)
        write_csv(args.detail_output, detail)
        write_csv(args.summary_output, summarize(detail))
        print(args.detail_output)
        print(args.summary_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
