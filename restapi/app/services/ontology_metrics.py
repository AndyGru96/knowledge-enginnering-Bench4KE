"""Documentation-completeness calculations for independently parsed RDF graphs."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import mean
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from restapi.app.utils.ontology_artifacts import DEFAULT_BASE, graph_flags, parse_turtle

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
        if predicate in (LABEL_PREDICATES | DOCUMENTATION_PREDICATES) and isinstance(_value, Literal) and str(_value).strip()
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
        "metric_status": "empty_ontology" if not len(graph) else ("available" if assessed_entities else "no_evaluable_entities"),
        **graph_flags(graph),
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
    }


def analyze_ontology(path: Path, base: str = DEFAULT_BASE) -> dict[str, Any]:
    graph, error = parse_turtle(path.read_bytes().decode("utf-8"), base)
    if graph is None:
        raise ValueError(error)
    return analyze_graph(graph)


def unavailable_metrics() -> dict[str, Any]:
    fields = list(analyze_graph(Graph()))
    row = {field: "" for field in fields}
    row["metric_status"] = "metric_unavailable_unparseable_ontology"
    row["metric_unavailable_reason"] = (
        "parse_success=false; the unknown entity denominator is not set to zero"
    )
    return row


