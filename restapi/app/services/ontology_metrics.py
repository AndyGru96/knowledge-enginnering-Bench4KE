from typing import Any, Dict

from rdflib import Graph
from rdflib.namespace import OWL, RDF, RDFS


def _rdflib_format(ontology_format: str) -> str:
    fmt = (ontology_format or "ttl").lower().lstrip(".")
    return {
        "ttl": "turtle",
        "turtle": "turtle",
        "rdf": "xml",
        "rdfxml": "xml",
        "xml": "xml",
        "owl": "xml",
        "jsonld": "json-ld",
        "json-ld": "json-ld",
        "nt": "nt",
        "ntriples": "nt",
        "n3": "n3",
    }.get(fmt, "turtle")


def compute_ontometrics(ontology_text: str, ontology_format: str = "ttl") -> Dict[str, Any]:
    graph = Graph()
    graph.parse(data=ontology_text, format=_rdflib_format(ontology_format))

    classes = set(graph.subjects(RDF.type, OWL.Class)) | set(
        graph.subjects(RDF.type, RDFS.Class)
    )
    object_properties = set(graph.subjects(RDF.type, OWL.ObjectProperty))
    datatype_properties = set(graph.subjects(RDF.type, OWL.DatatypeProperty))
    annotation_properties = set(graph.subjects(RDF.type, OWL.AnnotationProperty))
    individuals = set(graph.subjects(RDF.type, OWL.NamedIndividual))

    subclass_axioms = sum(1 for _ in graph.triples((None, RDFS.subClassOf, None)))
    equivalence_axioms = sum(
        1 for _ in graph.triples((None, OWL.equivalentClass, None))
    )
    disjoint_axioms = sum(1 for _ in graph.triples((None, OWL.disjointWith, None)))
    restriction_axioms = sum(1 for _ in graph.triples((None, RDF.type, OWL.Restriction)))

    classes_count = len(classes)
    object_properties_count = len(object_properties)
    datatype_properties_count = len(datatype_properties)
    annotation_properties_count = len(annotation_properties)
    individuals_count = len(individuals)

    attribute_richness = (
        datatype_properties_count / classes_count if classes_count else 0.0
    )
    relationship_richness = (
        object_properties_count
        / (object_properties_count + datatype_properties_count)
        if (object_properties_count + datatype_properties_count)
        else 0.0
    )
    avg_subclass_per_class = (
        subclass_axioms / classes_count if classes_count else 0.0
    )

    return {
        "triples_count": len(graph),
        "classes_count": classes_count,
        "object_properties_count": object_properties_count,
        "datatype_properties_count": datatype_properties_count,
        "annotation_properties_count": annotation_properties_count,
        "individuals_count": individuals_count,
        "subclass_axioms": subclass_axioms,
        "equivalence_axioms": equivalence_axioms,
        "disjoint_axioms": disjoint_axioms,
        "restriction_axioms": restriction_axioms,
        "attribute_richness": attribute_richness,
        "relationship_richness": relationship_richness,
        "avg_subclass_per_class": avg_subclass_per_class,
    }
