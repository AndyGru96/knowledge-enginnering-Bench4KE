from __future__ import annotations

import csv
from pathlib import Path

import pytest
from rdflib import Graph

from scripts.analyze_documentation_completeness import analyze_graph, analyze_tasks, summarize


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_documentation_metric_counts_supported_annotations() -> None:
    graph = Graph().parse(
        data="""
            @prefix : <https://example.org/test#> .
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
            @prefix skos: <http://www.w3.org/2004/02/skos/core#> .

            <https://example.org/test> a owl:Ontology ;
                rdfs:comment "Ontology-level documentation text." .
            :DocumentedClass a owl:Class ;
                rdfs:label "Documented class" ;
                skos:definition "A sufficiently detailed natural language definition." .
            :UndocumentedClass a owl:Class .
            :relatesTo a owl:ObjectProperty ; rdfs:label "relates to" .
            :quantity a owl:DatatypeProperty ;
                rdfs:comment "A sufficiently detailed datatype property description." .
        """,
        format="turtle",
    )
    result = analyze_graph(graph)
    assert result["class_count"] == 2
    assert result["class_label_coverage"] == 0.5
    assert result["class_comment_definition_coverage"] == 0.5
    assert result["object_property_label_coverage"] == 1.0
    assert result["object_property_comment_definition_coverage"] == 0.0
    assert result["datatype_property_label_coverage"] == 0.0
    assert result["datatype_property_comment_definition_coverage"] == 1.0
    assert result["ontology_metadata_presence"] is True
    assert result["nontrivial_comment_definition_entity_count"] == 2


def test_documentation_result_tables_reconcile() -> None:
    detail = read_csv(ROOT / "results" / "c2_documentation_completeness.csv")
    assert len(detail) == 153
    assert len({row["task_id"] for row in detail}) == 153
    assert sum(row["metric_status"] == "available" for row in detail) == 101
    assert sum(
        row["metric_status"] == "metric_unavailable_unparseable_final_ontology"
        for row in detail
    ) == 52
    assert all("stage" not in row and "evidence_admission" not in row for row in detail)

    total = next(
        row
        for row in summarize(detail)
        if row["method"] == "TOTAL" and row["prompt_variant"] == "ALL"
    )
    assert total["metric_available_tasks"] == 101
    assert total["metric_availability_rate"] == pytest.approx(0.660130719)
    assert total["class_label_coverage"] == pytest.approx(0.772727273)
    assert total["class_comment_definition_coverage"] == pytest.approx(0.43914956)
    assert total["object_property_label_coverage"] == pytest.approx(0.855355747)
    assert total["datatype_property_comment_definition_coverage"] == pytest.approx(
        0.606941082
    )


def test_documentation_summary_matches_recomputed_values() -> None:
    detail = read_csv(ROOT / "results" / "c2_documentation_completeness.csv")
    saved = read_csv(ROOT / "results" / "c2_documentation_summary.csv")
    generated = summarize(detail)
    assert len(saved) == len(generated) == 13
    for saved_row, generated_row in zip(saved, generated):
        assert saved_row.keys() == generated_row.keys()
        for field, value in generated_row.items():
            assert saved_row[field] == str(value)


def test_batch_analysis_reads_final_ontology_paths(tmp_path: Path) -> None:
    ontology = tmp_path / "ontology.ttl"
    ontology.write_text(
        """
        @prefix : <https://example.org/test#> .
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        :Documented a owl:Class ; rdfs:label "Documented" .
        """,
        encoding="utf-8",
    )
    rows = analyze_tasks(
        [
            {
                "task_id": "task-1",
                "dataset_id": "dataset-1",
                "method": "ontogenia",
                "prompt_variant": "P0",
                "raw_parse_success": "true",
                "normalized_parse_success": "true",
                "final_parse_success": "true",
                "ontology_path": "ontology.ttl",
            },
            {
                "task_id": "task-2",
                "dataset_id": "dataset-2",
                "method": "ontogenia",
                "prompt_variant": "P0",
                "raw_parse_success": "false",
                "normalized_parse_success": "false",
                "final_parse_success": "false",
                "ontology_path": "missing.ttl",
            },
        ],
        tmp_path,
    )
    assert rows[0]["class_label_coverage"] == 1.0
    assert rows[1]["metric_status"] == "metric_unavailable_unparseable_final_ontology"
