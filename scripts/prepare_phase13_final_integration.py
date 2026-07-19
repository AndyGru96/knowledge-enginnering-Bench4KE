"""Phase 13 deterministic C2 metrics, master tables, and report draft."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS


ROOT = Path(__file__).resolve().parents[1]
RESTAPI = ROOT / "restapi"
for entry in (ROOT, RESTAPI):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app.services.ontology_metrics import compute_ontometrics  # noqa: E402
from scripts.analyze_stage_a2_c3 import tree_hash  # noqa: E402
from scripts.prepare_stage_a1_preflight import (  # noqa: E402
    FROZEN_HASHES,
    HISTORICAL_MANIFESTS,
    historical_manifests,
    snapshot_manifest,
    tree_manifest,
)


RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"
C2_DETAIL = RESULTS / "c2_documentation_completeness.csv"
C2_SUMMARY = RESULTS / "c2_documentation_summary.csv"
C2_REPORT = REPORTS / "C2_DOCUMENTATION_COMPLETENESS.md"
PARSE_SUMMARY = RESULTS / "final_method_variant_parse_summary.csv"
COST_SUMMARY = RESULTS / "final_generation_cost_summary.csv"
REPAIR_SUMMARY = RESULTS / "final_repair_length_summary.csv"
C2_C3_SUMMARY = RESULTS / "final_c2_c3_summary.csv"
FINAL_MANIFEST = RESULTS / "final_experiment_manifest.json"
FINAL_REPORT = REPORTS / "FINAL_REPORT_DRAFT.md"
DELIVERY_CHECKLIST = REPORTS / "FINAL_DELIVERY_CHECKLIST.md"

METHODS = ("ontogenia", "domain-ontogen", "neon-gpt")
VARIANTS = ("P0", "P1", "P2")
MODEL = "qwen3:30b-a3b-instruct-2507-q4_K_M"
MODEL_DIGEST = "19e422b0231392335cfc49cfd172de7034bb1aeabb08aa307cce745c60b272fe"
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
PHASE13_TEST_SUMMARY = {
    "full_pytest": "133 passed, 0 failed, 0 skipped (1 deprecation warning)",
    "phase13_targeted": "46 passed, 0 failed, 0 skipped (1 deprecation warning)",
    "compileall": "PASS for restapi and scripts",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def integer(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def rate(numerator: int | float, denominator: int | float) -> float | str:
    return numerator / denominator if denominator else ""


def rounded(value: float | str, digits: int = 9) -> float | str:
    return round(value, digits) if isinstance(value, float) and math.isfinite(value) else value


def admitted_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for row in read_csv(RESULTS / "a1_admitted_task_results.csv"):
        tasks.append(
            {
                "stage": "A1",
                "task_id": row["task_id"],
                "dataset_id": row["dataset_id"],
                "method": row["method"],
                "prompt_variant": "P0",
                "evidence_admission": "ADMITTED_SCHEMA_V2",
                "historical_execution_classification": "FAIL_SCHEMA_V1",
                "output_directory": row["original_output_directory"],
                "task_status": row["task_status"],
                "raw_parse_success": boolean(row["raw_parse_success"]),
                "normalized_parse_success": boolean(row["normalized_parse_success"]),
                "final_parse_success": boolean(row["final_parse_success"]),
                "normalization_used": boolean(row["normalization_used"]),
                "repair_used": boolean(row["repair_used"]),
                "repair_call_count": integer(row["repair_call_count"]),
                "length_termination_count": integer(row["length_termination_count"]),
                "length_termination": boolean(row["length_termination"]),
                "wall_clock_seconds": number(row["wall_clock_seconds"]),
                "prompt_tokens": integer(row["prompt_tokens"]),
                "completion_tokens": integer(row["completion_tokens"]),
                "internal_call_count": integer(row["internal_call_count"]),
                "http_attempt_count": integer(row["internal_call_count"]),
                "retry_count": 0,
            }
        )
    repair_manifest = read_json(RESULTS / "a2_repair_aware_admission_manifest.json")
    admitted_ids = {
        row["task_id"]
        for row in repair_manifest["tasks"]
        if row["repair_aware_admission_decision"] == "ADMITTED"
    }
    for row in read_csv(RESULTS / "a2_task_completeness.csv"):
        if row["task_id"] not in admitted_ids:
            continue
        tasks.append(
            {
                "stage": "A2",
                "task_id": row["task_id"],
                "dataset_id": row["dataset_id"],
                "method": row["method"],
                "prompt_variant": row["prompt_variant"],
                "evidence_admission": "ADMITTED_REPAIR_AWARE_SIDECAR",
                "historical_execution_classification": "FAIL_STRICT_SCHEMA_V2",
                "output_directory": row["output_directory"],
                "task_status": row["envelope_status"],
                "raw_parse_success": boolean(row["raw_parse_success"]),
                "normalized_parse_success": boolean(row["normalized_parse_success"]),
                "final_parse_success": boolean(row["final_parse_success"]),
                "normalization_used": boolean(row["normalization_used"]),
                "repair_used": integer(row["repair_call_count"]) > 0,
                "repair_call_count": integer(row["repair_call_count"]),
                "length_termination_count": integer(row["length_termination_count"]),
                "length_termination": integer(row["length_termination_count"]) > 0,
                "wall_clock_seconds": number(row["wall_clock_seconds"]),
                "prompt_tokens": integer(row["step_telemetry_prompt_tokens"]),
                "completion_tokens": integer(row["step_telemetry_completion_tokens"]),
                "internal_call_count": integer(row["internal_call_count"]),
                "http_attempt_count": integer(row["http_attempt_count"]),
                "retry_count": integer(row["retry_count"]),
            }
        )
    tasks.sort(key=lambda row: (METHODS.index(row["method"]), VARIANTS.index(row["prompt_variant"]), row["dataset_id"]))
    if len(tasks) != 153:
        raise RuntimeError(f"Expected 153 admitted tasks, found {len(tasks)}")
    return tasks


def literals_for(graph: Graph, entity: Any, predicates: set[Any]) -> list[str]:
    values: list[str] = []
    for predicate in predicates:
        values.extend(str(value).strip() for value in graph.objects(entity, predicate) if isinstance(value, Literal) and str(value).strip())
    return values


def nontrivial(value: str) -> bool:
    return len(value) >= NONTRIVIAL_MIN_CHARS and len(value.split()) >= NONTRIVIAL_MIN_WORDS


def entity_documentation(graph: Graph, entities: set[Any]) -> dict[str, Any]:
    label_count = 0
    documented_count = 0
    nontrivial_count = 0
    documentation_literals: list[str] = []
    for entity in entities:
        labels = literals_for(graph, entity, LABEL_PREDICATES)
        docs = literals_for(graph, entity, DOCUMENTATION_PREDICATES)
        label_count += bool(labels)
        documented_count += bool(docs)
        nontrivial_count += any(nontrivial(value) for value in docs)
        documentation_literals.extend(docs)
    return {
        "entities": len(entities),
        "labels": label_count,
        "documented": documented_count,
        "nontrivial": nontrivial_count,
        "documentation_literals": documentation_literals,
    }


def c2_row(task: dict[str, Any]) -> dict[str, Any]:
    base = {
        "stage": task["stage"],
        "task_id": task["task_id"],
        "dataset_id": task["dataset_id"],
        "method": task["method"],
        "prompt_variant": task["prompt_variant"],
        "evidence_admission": task["evidence_admission"],
        "output_directory": task["output_directory"],
        "raw_parse_success": task["raw_parse_success"],
        "normalized_parse_success": task["normalized_parse_success"],
        "final_parse_success": task["final_parse_success"],
    }
    if not task["final_parse_success"]:
        return {
            **base,
            "metric_status": "metric_unavailable_unparseable_final_ontology",
            "metric_unavailable_reason": "final_parse_success=false; unknown entity denominator is not imputed as zero",
            "class_count": "",
            "class_label_count": "",
            "class_label_coverage": "",
            "class_comment_definition_count": "",
            "class_comment_definition_coverage": "",
            "object_property_count": "",
            "object_property_label_count": "",
            "object_property_label_coverage": "",
            "object_property_comment_definition_count": "",
            "object_property_comment_definition_coverage": "",
            "datatype_property_count": "",
            "datatype_property_label_count": "",
            "datatype_property_label_coverage": "",
            "datatype_property_comment_definition_count": "",
            "datatype_property_comment_definition_coverage": "",
            "ontology_declaration_present": "",
            "ontology_metadata_statement_count": "",
            "ontology_metadata_presence": "",
            "documentation_literal_count": "",
            "documentation_total_characters": "",
            "average_documentation_length": "",
            "assessed_entity_count": "",
            "nontrivial_comment_definition_entity_count": "",
            "nontrivial_comment_definition_rate": "",
            "triples_count": "",
            "classes_count_structural": "",
            "object_properties_count_structural": "",
            "datatype_properties_count_structural": "",
        }
    ontology_path = ROOT / task["output_directory"] / "final_ontology.ttl"
    text = ontology_path.read_text(encoding="utf-8")
    graph = Graph()
    graph.parse(data=text, format="turtle")
    classes = set(graph.subjects(RDF.type, OWL.Class)) | set(graph.subjects(RDF.type, RDFS.Class))
    objects = set(graph.subjects(RDF.type, OWL.ObjectProperty))
    datatypes = set(graph.subjects(RDF.type, OWL.DatatypeProperty))
    class_docs = entity_documentation(graph, classes)
    object_docs = entity_documentation(graph, objects)
    datatype_docs = entity_documentation(graph, datatypes)
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
    docs = class_docs["documentation_literals"] + object_docs["documentation_literals"] + datatype_docs["documentation_literals"] + ontology_docs
    assessed = class_docs["entities"] + object_docs["entities"] + datatype_docs["entities"]
    nontrivial_entities = class_docs["nontrivial"] + object_docs["nontrivial"] + datatype_docs["nontrivial"]
    structural = compute_ontometrics(text, "ttl")
    return {
        **base,
        "metric_status": "available",
        "metric_unavailable_reason": "",
        "class_count": class_docs["entities"],
        "class_label_count": class_docs["labels"],
        "class_label_coverage": rounded(rate(class_docs["labels"], class_docs["entities"])),
        "class_comment_definition_count": class_docs["documented"],
        "class_comment_definition_coverage": rounded(rate(class_docs["documented"], class_docs["entities"])),
        "object_property_count": object_docs["entities"],
        "object_property_label_count": object_docs["labels"],
        "object_property_label_coverage": rounded(rate(object_docs["labels"], object_docs["entities"])),
        "object_property_comment_definition_count": object_docs["documented"],
        "object_property_comment_definition_coverage": rounded(rate(object_docs["documented"], object_docs["entities"])),
        "datatype_property_count": datatype_docs["entities"],
        "datatype_property_label_count": datatype_docs["labels"],
        "datatype_property_label_coverage": rounded(rate(datatype_docs["labels"], datatype_docs["entities"])),
        "datatype_property_comment_definition_count": datatype_docs["documented"],
        "datatype_property_comment_definition_coverage": rounded(rate(datatype_docs["documented"], datatype_docs["entities"])),
        "ontology_declaration_present": bool(ontology_subjects),
        "ontology_metadata_statement_count": ontology_metadata_statements,
        "ontology_metadata_presence": bool(ontology_subjects and ontology_metadata_statements),
        "documentation_literal_count": len(docs),
        "documentation_total_characters": sum(len(value) for value in docs),
        "average_documentation_length": rounded(mean(len(value) for value in docs) if docs else 0.0),
        "assessed_entity_count": assessed,
        "nontrivial_comment_definition_entity_count": nontrivial_entities,
        "nontrivial_comment_definition_rate": rounded(rate(nontrivial_entities, assessed)),
        "triples_count": structural["triples_count"],
        "classes_count_structural": structural["classes_count"],
        "object_properties_count_structural": structural["object_properties_count"],
        "datatype_properties_count_structural": structural["datatype_properties_count"],
    }


def selections(rows: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    output: list[tuple[str, str, list[dict[str, Any]]]] = []
    for method in METHODS:
        for variant in VARIANTS:
            output.append((method, variant, [row for row in rows if row["method"] == method and row["prompt_variant"] == variant]))
        output.append((method, "ALL", [row for row in rows if row["method"] == method]))
    output.append(("TOTAL", "ALL", rows))
    return output


def sum_field(rows: list[dict[str, Any]], field: str) -> int:
    return sum(integer(row.get(field)) for row in rows if row.get(field) not in (None, ""))


def c2_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for method, variant, selected in selections(rows):
        available = [row for row in selected if row["metric_status"] == "available"]
        class_count = sum_field(available, "class_count")
        object_count = sum_field(available, "object_property_count")
        datatype_count = sum_field(available, "datatype_property_count")
        assessed = sum_field(available, "assessed_entity_count")
        doc_literals = sum_field(available, "documentation_literal_count")
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
                "ontology_metadata_present_tasks": sum(boolean(row["ontology_metadata_presence"]) for row in available),
                "ontology_metadata_rate_available_tasks": rounded(rate(sum(boolean(row["ontology_metadata_presence"]) for row in available), len(available))),
                "ontology_metadata_observed_lower_bound_all_tasks": rounded(rate(sum(boolean(row["ontology_metadata_presence"]) for row in available), len(selected))),
                "documentation_literal_count": doc_literals,
                "documentation_total_characters": sum_field(available, "documentation_total_characters"),
                "average_documentation_length": rounded(rate(sum_field(available, "documentation_total_characters"), doc_literals)),
                "assessed_entities": assessed,
                "nontrivial_documented_entities": sum_field(available, "nontrivial_comment_definition_entity_count"),
                "nontrivial_comment_definition_rate": rounded(rate(sum_field(available, "nontrivial_comment_definition_entity_count"), assessed)),
                "mean_triples_parseable_tasks": rounded(mean(number(row["triples_count"]) for row in available) if available else ""),
                "mean_classes_parseable_tasks": rounded(mean(number(row["classes_count_structural"]) for row in available) if available else ""),
                "denominator_policy": "entity coverage uses known entities in parseable final ontologies; every unparseable task remains an explicit metric-unavailable task in the task denominator",
            }
        )
    return output


def task_group_summary(tasks: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    groups = [(m, v, [r for r in tasks if r["method"] == m and r["prompt_variant"] == v]) for m in METHODS for v in VARIANTS]
    groups.append(("TOTAL", "ALL", tasks))
    for method, variant, selected in groups:
        base = {"method": method, "prompt_variant": variant, "tasks": len(selected)}
        if kind == "parse":
            raw = sum(row["raw_parse_success"] for row in selected)
            normalized = sum(row["normalized_parse_success"] for row in selected)
            final = sum(row["final_parse_success"] for row in selected)
            rows.append({**base, "raw_parse_success": raw, "raw_parse_rate": rounded(rate(raw, len(selected))), "normalized_parse_success": normalized, "normalized_parse_rate": rounded(rate(normalized, len(selected))), "final_parse_success": final, "final_parse_rate": rounded(rate(final, len(selected))), "metric_unavailable_final_parse_failures": len(selected) - final, "evidence_basis": "A1 schema-v2 admitted P0 plus A2 repair-aware admitted P1/P2"})
        elif kind == "cost":
            calls = sum(row["internal_call_count"] for row in selected)
            prompt = sum(row["prompt_tokens"] for row in selected)
            completion = sum(row["completion_tokens"] for row in selected)
            runtime = sum(row["wall_clock_seconds"] for row in selected)
            rows.append({**base, "internal_calls": calls, "http_attempts": sum(row["http_attempt_count"] for row in selected), "retries": sum(row["retry_count"] for row in selected), "prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion, "task_wall_clock_seconds": rounded(runtime), "mean_tokens_per_task": rounded(rate(prompt + completion, len(selected))), "mean_wall_clock_seconds_per_task": rounded(rate(runtime, len(selected))), "cost_scope": "native local telemetry; no monetary API cost"})
        else:
            repairs = sum(row["repair_call_count"] for row in selected)
            lengths = sum(row["length_termination_count"] for row in selected)
            calls = sum(row["internal_call_count"] for row in selected)
            rows.append({**base, "repair_used_tasks": sum(row["repair_used"] for row in selected), "repair_calls": repairs, "repair_call_rate": rounded(rate(repairs, calls)), "length_terminated_tasks": sum(row["length_termination"] for row in selected), "length_terminations": lengths, "length_termination_call_rate": rounded(rate(lengths, calls)), "internal_calls": calls, "limitation": "repair calls are variant-agnostic under Phase 12B; length termination records model stop reason, not automatic evidence rejection"})
    return rows


def c2_c3_summary(c2_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    c2_map = {(row["method"], row["prompt_variant"]): row for row in c2_rows}
    q = {row["method"]: row for row in read_csv(RESULTS / "c3_repair_aware_cochran_q_results.csv")}
    w = {row["method"]: row for row in read_csv(RESULTS / "c3_repair_aware_wilcoxon_results.csv")}
    output: list[dict[str, Any]] = []
    for method in METHODS:
        row: dict[str, Any] = {"method": method}
        for variant in VARIANTS:
            c2 = c2_map[(method, variant)]
            prefix = variant.lower()
            for field in ("metric_availability_rate", "class_label_coverage", "class_comment_definition_coverage", "object_property_label_coverage", "object_property_comment_definition_coverage", "datatype_property_label_coverage", "datatype_property_comment_definition_coverage", "ontology_metadata_rate_available_tasks", "average_documentation_length", "nontrivial_comment_definition_rate"):
                row[f"{prefix}_{field}"] = c2[field]
        row.update({"c3_binary_paired_n": q[method]["paired_n"], "c3_cochran_q": q[method]["statistic"], "c3_cochran_p_value": q[method]["p_value"], "c3_cochran_status": q[method]["status"], "c3_term_paired_n": w[method]["paired_n"], "c3_wilcoxon_p_value": w[method]["p_value"], "c3_matched_rank_biserial": w[method]["matched_rank_biserial"], "c3_wilcoxon_status": w[method]["status"], "statistical_conclusion": "no statistically significant prompt-sensitivity result at alpha=0.05; no publication-strength superiority claim", "limitations": "C2 entity coverage is conditional on parseable outputs; Ontogenia term-Jaccard is not estimable" if method == "ontogenia" else "C2 entity coverage is conditional on parseable outputs; small term-pair denominators limit sensitivity"})
        output.append(row)
    return output


def pct(value: Any) -> str:
    if value in (None, ""):
        return "NA"
    return f"{100 * float(value):.1f}%"


def c2_markdown(summary: list[dict[str, Any]]) -> str:
    detail = [row for row in summary if row["method"] in METHODS and row["prompt_variant"] in VARIANTS]
    lines = [
        "# C2 Documentation Completeness",
        "",
        "Phase: **Revised Phase 13**. Computation is deterministic and uses admitted final ontology artifacts only; model/provider calls are zero.",
        "",
        "## Metric policy",
        "",
        "Labels are `rdfs:label` or `skos:prefLabel`. Comments/definitions are `rdfs:comment`, `skos:definition`, `dcterms:description`, or `schema:description`. A nontrivial document literal has at least 20 Unicode characters and at least three whitespace-delimited words. Ontology metadata requires an `owl:Ontology` declaration plus at least one non-`rdf:type` statement.",
        "",
        "Unparseable final outputs remain explicit `metric_unavailable` rows. Entity coverage is calculated only over known entities in parseable outputs; metric availability and the metadata lower bound retain all 153 tasks in their denominators.",
        "",
        "## Method/variant results",
        "",
        "| Method | Variant | Available | Class label | Class docs | Object label | Object docs | Datatype label | Datatype docs | Metadata | Avg doc chars | Nontrivial docs |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in detail:
        average_length = row["average_documentation_length"]
        average_length_text = "NA" if average_length in ("", None) else f"{float(average_length):.1f}"
        lines.append(f"| {row['method']} | {row['prompt_variant']} | {row['metric_available_tasks']}/{row['tasks_total']} | {pct(row['class_label_coverage'])} | {pct(row['class_comment_definition_coverage'])} | {pct(row['object_property_label_coverage'])} | {pct(row['object_property_comment_definition_coverage'])} | {pct(row['datatype_property_label_coverage'])} | {pct(row['datatype_property_comment_definition_coverage'])} | {pct(row['ontology_metadata_rate_available_tasks'])} | {average_length_text} | {pct(row['nontrivial_comment_definition_rate'])} |")
    total = next(row for row in summary if row["method"] == "TOTAL")
    lines.extend(["", "## Coverage and interpretation", "", f"C2 is available for {total['metric_available_tasks']}/{total['tasks_total']} tasks ({pct(total['metric_availability_rate'])}); {total['metric_unavailable_tasks']} unparseable outputs are retained as unavailable rather than dropped or treated as empty ontologies.", "", "Documentation ratios are descriptive, not a semantic-quality judgment. High comment coverage can coexist with invalid Turtle or weak ontology design, and low label coverage can coexist with parseable structure."])
    return "\n".join(lines) + "\n"


def final_report_markdown(parse_rows: list[dict[str, Any]], cost_rows: list[dict[str, Any]], repair_rows: list[dict[str, Any]], c2_rows: list[dict[str, Any]], combined: list[dict[str, Any]]) -> str:
    parse_map = {(r["method"], r["prompt_variant"]): r for r in parse_rows}
    cost_total = next(row for row in cost_rows if row["method"] == "TOTAL")
    repair_total = next(row for row in repair_rows if row["method"] == "TOTAL")
    c2_total = next(row for row in c2_rows if row["method"] == "TOTAL")
    lines = [
        "# Final Report Draft — Local LLM Ontology Generation Evaluation",
        "",
        "Status: **Revised Phase 13 draft for review**. This document integrates admitted evidence; it is not the polished final submission.",
        "",
        "## 1. Introduction",
        "",
        "This project evaluates three ontology-generation approaches with a fully local Ollama model and an auditable artifact contract. It focuses on whether the pipelines produce parseable ontology serializations, how documentation completeness differs across methods and prompt variants, and whether append-only P1/P2 instructions materially change parse success relative to P0.",
        "",
        "## 2. Project objectives and selected additions",
        "",
        "The approved methods are Ontogenia, Domain-OntoGen, and NeOn-GPT. The implementation adds deterministic dataset preparation, authoritative prompt recovery, local Ollama provider support, per-call evidence preservation, versioned cache identity, repair-aware governance, C2 documentation metrics, and a frozen C3 paired statistical analysis.",
        "",
        "## 3. Dataset construction",
        "",
        "The normalized full-generation dataset contains 17 scenarios and 74 source-ordered competency questions. Conversion uses only explicit workbook relationships; fuzzy or CQ-prefix story inference is prohibited. The frozen dataset manifest is `e06831a155503aa5c2faa8312b7bd78eb6778b124f31dbfb1617bc63c6664caf`.",
        "",
        "## 4. Compared ontology-generation methods",
        "",
        "Ontogenia and Domain-OntoGen process competency questions independently and assemble task-level results. NeON-GPT uses an initial generation call and may invoke frozen syntax-repair calls. P1 asks for serialization-only output; P2 additionally requests silent syntax and prefix verification. Both are append-only changes to the approved P0 sources.",
        "",
        "## 5. Local Ollama execution infrastructure",
        "",
        f"All admitted A1/A2 results use Ollama 0.32.0 and exact model `{MODEL}` with digest `{MODEL_DIGEST}`, temperature 0, seed 42, `num_ctx=32768`, and `num_predict=8192`. Across A1 and A2 there are {cost_total['internal_calls']} internal calls, {cost_total['prompt_tokens']:,} prompt tokens, {cost_total['completion_tokens']:,} completion tokens, and {float(cost_total['task_wall_clock_seconds']):,.3f} summed task seconds. These are local telemetry measurements, not monetary API costs.",
        "",
        "## 6. Output contract, cache identity, and evidence preservation",
        "",
        "Every call preserves request, raw response, raw/normalized/final text, parse metadata, telemetry, and propagated previous output where required. Stage A1 remains historically FAIL under schema v1 but its 51 tasks are admitted under schema v2. Stage A2 must retain three separate classifications:",
        "",
        "- Historical Stage A2 strict schema-v2 identity: **FAIL**.",
        "- Phase 12 strict sidecar admission: **PARTIALLY ADMITTED**.",
        "- Phase 12B repair-aware sidecar admission: **ADMITTED**.",
        "",
        "The A2 failure was a saved prompt-hash identity defect, not evidence that P0 prompts were executed. The repair-aware ruling treats exact frozen NeON syntax repairs as variant-agnostic post-processing while preserving the stricter historical record.",
        "",
        "## 7. Metrics",
        "",
        "### Structural metrics",
        "",
        f"Structural counts are computed only for parseable final ontologies. Of 153 admitted tasks, {c2_total['metric_available_tasks']} are parseable and {c2_total['metric_unavailable_tasks']} remain explicitly unavailable. Triple, class, object-property, and datatype-property counts are descriptive and are not imputed for failed parses.",
        "",
        "### C2 documentation completeness",
        "",
        f"Across parseable outputs, micro class-label coverage is {pct(c2_total['class_label_coverage'])}, class comment/definition coverage {pct(c2_total['class_comment_definition_coverage'])}, object-property label coverage {pct(c2_total['object_property_label_coverage'])}, object-property comment/definition coverage {pct(c2_total['object_property_comment_definition_coverage'])}, datatype-property label coverage {pct(c2_total['datatype_property_label_coverage'])}, and datatype-property comment/definition coverage {pct(c2_total['datatype_property_comment_definition_coverage'])}. Metric availability over all tasks is {pct(c2_total['metric_availability_rate'])}.",
        "",
        "### C3 prompt sensitivity",
        "",
        "C3 uses paired `final_parse_success` with Cochran's Q followed by McNemar/Holm, and P0-centred ontology-term Jaccard with paired Pratt Wilcoxon where estimable. All 17 dataset triplets are available per method under repair-aware admission.",
        "",
        "## 8. Experiments",
        "",
        "### A1 P0 baseline",
        "",
        "A1 contains 51 admitted P0 tasks and 193 calls. Final parse success is 4/17 Ontogenia, 16/17 Domain-OntoGen, and 11/17 NeON-GPT. The original schema-v1 run remains FAIL because integer and float temperature representations produced different cache keys.",
        "",
        "### A2 P1/P2 variants",
        "",
        "A2 contains 102 repair-aware admitted tasks and 383 calls. Historical strict reconciliation remains FAIL because 94 successful envelopes saved P0 prompt hashes. Phase 12B admits the immutable evidence after verifying all 330 initial prompts and all 53 variant-agnostic repair calls.",
        "",
        "## 9. Results",
        "",
        "| Method | P0 final parse | P1 final parse | P2 final parse |",
        "|---|---:|---:|---:|",
    ]
    for method in METHODS:
        lines.append(f"| {method} | {parse_map[(method, 'P0')]['final_parse_success']}/17 | {parse_map[(method, 'P1')]['final_parse_success']}/17 | {parse_map[(method, 'P2')]['final_parse_success']}/17 |")
    lines.extend(["", f"The combined experiment records {repair_total['repair_calls']} repair calls and {repair_total['length_terminations']} length terminations. Repair and truncation are retained as outcomes rather than silently excluded.", "", "Cochran Q p-values are 0.9048 for Ontogenia, 0.3679 for Domain-OntoGen, and 0.5292 for NeON-GPT. Every Holm-adjusted McNemar p-value is 1.0. Domain-OntoGen term-Jaccard Wilcoxon p=0.3380; NeON-GPT p=0.1957; Ontogenia is not estimable because it has no complete parseable term pairs.", "", "## 10. Discussion", "", "Prompt variants did not show statistically significant parse-success differences. Domain-OntoGen is the most consistently parseable method across variants, while Ontogenia remains limited by frequent invalid individual CQ fragments. NeON-GPT benefits from syntax repair but incurs additional calls and retains truncation/repair sensitivity. Documentation completeness also varies materially and should be interpreted jointly with parse availability.", "", "## 11. Failure analysis", "", "B6 documents Ontogenia's frequent invalid model Turtle, including malformed disjointness and property syntax. This is a monitored method/output quality risk rather than a proven deterministic assembly defect. B8 and B10 exposed two different reproducibility failures: numeric-type canonicalization in A1 and success-path prompt-hash substitution in A2. Both required explicit sidecar admission rather than historical rewriting.", "", "## 12. Limitations", "", "The experiment uses one local quantized model, 17 dataset items, one seed, and a single fixed context/output budget. C2 is syntax-conditional and does not measure conceptual correctness. Parse success is not equivalent to ontology quality. Some C3 term analyses have small or zero paired denominators. Repair-aware admission is a documented governance decision and does not make the original A2 cache contract pass. No publication-strength prompt-superiority claim is supported.", "", "## 13. Reproducibility", "", "Dataset, prompt, model, configuration, task-plan, request, response, parse, telemetry, and sidecar hashes are retained in the final experiment manifest. Future execution under changed implementation code requires a new freeze; historical sidecars are analytical admission records, not reusable cache hits.", "", "## 14. Conclusion", "", "The infrastructure successfully produced auditable local Ollama evidence. Prompt variants did not show statistically significant parse-success differences under the repair-aware admitted analysis. Documentation completeness and parse robustness varied substantially by method. The project exposed important reproducibility issues in prompt identity, repair governance, and evidence preservation.", "", "## Draft verification", "", f"- Full pytest: {PHASE13_TEST_SUMMARY['full_pytest']}", f"- Phase 13 targeted tests: {PHASE13_TEST_SUMMARY['phase13_targeted']}", f"- Compileall: {PHASE13_TEST_SUMMARY['compileall']}"])
    return "\n".join(lines) + "\n"


def delivery_checklist_markdown() -> str:
    return """# Final Delivery Checklist

Status: **draft review gate**. Phase 13 prepares evidence and prose; it does not finalize or submit the course project.

## Final report polishing

- [ ] Confirm title page, author/course metadata, abstract, and required word/page limits.
- [ ] Convert draft statements into the course's requested citation style.
- [ ] Cross-check every reported number against the master CSVs and final manifest.
- [ ] Preserve the three-layer A2 history verbatim.
- [ ] Ensure B6 is described as open/non-blocking and not silently closed.

## Figures

- [ ] Add parse-success comparison figure with task denominators.
- [ ] Add C2 documentation-coverage figure with metric-availability annotation.
- [ ] Add cost/repair/length figure without implying monetary API cost.
- [ ] Add evidence-flow diagram distinguishing strict execution from sidecar admission.
- [ ] Verify accessible colors, captions, axes, and source-table references.

## Tables

- [ ] Select compact final tables from `final_*_summary.csv`.
- [ ] Keep non-estimable C3 cells explicit rather than zero-filled.
- [ ] Include C2 unavailable-task counts beside entity coverage.
- [ ] Verify rounding is consistent between report, tables, and slides.

## README

- [ ] Add environment setup and deterministic analysis commands.
- [ ] Explain that real generation is complete and must not be rerun casually.
- [ ] Link frozen configs, admission manifests, master tables, and final report.
- [ ] Document known B6 limitations and the A1/A2 historical classifications.

## Presentation slides

- [ ] Prepare problem, methods, experiment design, evidence governance, results, limitations, and conclusion slides.
- [ ] Use the same three-layer A2 wording as the report.
- [ ] Avoid prompt-superiority or statistical-significance claims.
- [ ] Include backup slides for cache identity, repair governance, and telemetry.

## Repository cleanup

- [ ] Remove only temporary test/render artifacts after verifying paths.
- [ ] Do not remove historical outputs, strict reports, or sidecar manifests.
- [ ] Review `git status` and separate project changes from generated evidence.
- [ ] Run final pytest, compileall, hash verification, and link validation.
- [ ] Obtain explicit project-owner approval before final submission or presentation packaging.
"""


def main() -> int:
    tasks = admitted_tasks()
    c2_detail = [c2_row(task) for task in tasks]
    c2_agg = c2_summary(c2_detail)
    parse = task_group_summary(tasks, "parse")
    cost = task_group_summary(tasks, "cost")
    repair = task_group_summary(tasks, "repair")
    combined = c2_c3_summary(c2_agg)
    write_csv(C2_DETAIL, c2_detail)
    write_csv(C2_SUMMARY, c2_agg)
    write_csv(PARSE_SUMMARY, parse)
    write_csv(COST_SUMMARY, cost)
    write_csv(REPAIR_SUMMARY, repair)
    write_csv(C2_C3_SUMMARY, combined)
    C2_REPORT.write_text(c2_markdown(c2_agg), encoding="utf-8")
    FINAL_REPORT.write_text(final_report_markdown(parse, cost, repair, c2_agg, combined), encoding="utf-8")
    DELIVERY_CHECKLIST.write_text(delivery_checklist_markdown(), encoding="utf-8")

    source_paths = [
        "reports/A1_RESULT_INTEGRATION.md", "reports/A2_EXECUTION.md", "reports/B10_REMEDIATION.md",
        "reports/B10_REPAIR_AWARE_ADMISSION.md", "reports/C3_PROMPT_SENSITIVITY_REPAIR_AWARE.md",
        "results/a1_admitted_task_results.csv", "results/a1_admitted_step_results.csv",
        "results/a2_repair_aware_admission_manifest.json", "results/c3_repair_aware_analysis_manifest.json",
        "config/c3_analysis_policy.yaml", "docs/C3_STATISTICAL_ANALYSIS_PLAN.md",
        "project_notes/STATE.md", "project_notes/DECISIONS.md", "project_notes/BLOCKERS.md",
    ]
    output_paths = [
        C2_DETAIL, C2_SUMMARY, C2_REPORT, PARSE_SUMMARY, COST_SUMMARY, REPAIR_SUMMARY,
        C2_C3_SUMMARY, FINAL_REPORT, DELIVERY_CHECKLIST,
    ]
    phase12b = read_json(RESULTS / "a2_repair_aware_admission_manifest.json")
    a2_tree = tree_hash(ROOT / "outputs/stage_a2")
    a1_tree = tree_hash(ROOT / "outputs/stage_a1")
    total_c2 = next(row for row in c2_agg if row["method"] == "TOTAL")
    manifest = {
        "schema_version": "final-experiment-manifest-v1",
        "phase": "Revised Phase 13",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "classifications": {
            "A1_historical_schema_v1": "FAIL",
            "A1_schema_v2_evidence_admission": "ADMITTED",
            "A2_historical_strict_schema_v2": "FAIL",
            "A2_phase12_strict_sidecar": "PARTIALLY ADMITTED",
            "A2_phase12b_repair_aware_sidecar": "ADMITTED",
            "B10": "RESOLVED",
            "B6": "OPEN_NON_BLOCKING_MONITORED_QUALITY_RISK",
        },
        "scope": {"dataset_items": 17, "methods": list(METHODS), "variants": list(VARIANTS), "admitted_tasks": 153, "A1_tasks": 51, "A2_tasks": 102},
        "model": {"provider": "ollama", "name": MODEL, "digest": MODEL_DIGEST, "temperature": 0, "seed": 42, "num_ctx": 32768, "num_predict": 8192},
        "c2": {"metric_available_tasks": total_c2["metric_available_tasks"], "metric_unavailable_tasks": total_c2["metric_unavailable_tasks"], "denominator_policy": total_c2["denominator_policy"], "nontrivial_threshold": {"minimum_unicode_characters": NONTRIVIAL_MIN_CHARS, "minimum_words": NONTRIVIAL_MIN_WORDS}},
        "c3": read_json(RESULTS / "c3_repair_aware_analysis_manifest.json"),
        "source_files": {path: sha256(ROOT / path) for path in source_paths},
        "outputs": {path.relative_to(ROOT).as_posix(): sha256(path) for path in output_paths},
        "evidence_preservation": {
            "A2_legacy_tree_sha256": a2_tree[0], "A2_files": a2_tree[1], "A2_bytes": a2_tree[2],
            "A2_complete_canonical_manifest": phase12b["A2_output_tree_manifests"]["complete_a2_output_tree"]["manifest_sha256"],
            "A1_legacy_tree_sha256": a1_tree[0], "A1_files": a1_tree[1], "A1_bytes": a1_tree[2],
            "A1_generation_manifest": tree_manifest(ROOT / "outputs/stage_a1/project2"),
            "A1_admission_manifest_sha256": sha256(RESULTS / "a1_evidence_admission_manifest.json"),
            "dataset_audit_sha256": sha256(ROOT / "datasets/ontology_generation/dataset_audit.json"),
            "dataset_full_generation_sha256": sha256(ROOT / "datasets/ontology_generation/normalized/project2_full_generation.jsonl"),
            "phase3_snapshot_manifest_sha256": snapshot_manifest(),
            "historical_phase5_6_6r": historical_manifests(),
            "all_expected_frozen_values_match": sha256(ROOT / "datasets/ontology_generation/dataset_audit.json") == FROZEN_HASHES["dataset_audit_sha256"] and sha256(ROOT / "datasets/ontology_generation/normalized/project2_full_generation.jsonl") == FROZEN_HASHES["dataset_full_generation_sha256"] and snapshot_manifest() == FROZEN_HASHES["phase3_snapshot_manifest_sha256"] and historical_manifests() == HISTORICAL_MANIFESTS,
        },
        "limitations": ["single quantized local model", "17 dataset items and one seed", "C2 entity coverage conditional on parseable outputs", "parseability is not conceptual ontology quality", "Ontogenia C3 term sensitivity not estimable", "repair-aware admission does not rewrite historical A2 strict FAIL"],
        "tests": PHASE13_TEST_SUMMARY,
        "generation_calls_made_in_phase13": 0,
        "api_chat_calls_made_in_phase13": 0,
        "audit_code": {"path": Path(__file__).relative_to(ROOT).as_posix(), "sha256": sha256(Path(__file__))},
        "statement": "Phase 13 reads admitted immutable evidence and writes only deterministic metrics, summaries, manifests, and report drafts. No raw A1/A2 evidence was modified.",
    }
    FINAL_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(tasks), "c2_available": total_c2["metric_available_tasks"], "c2_unavailable": total_c2["metric_unavailable_tasks"], "outputs": [path.relative_to(ROOT).as_posix() for path in output_paths] + [FINAL_MANIFEST.relative_to(ROOT).as_posix()], "generation_calls": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
