
from rdflib import Graph

from scripts.analyze_documentation_completeness import analyze_graph


def test_imports_do_not_count_as_documentation():
    graph = Graph().parse(data=(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "<https://example.org/ontology> a owl:Ontology ; owl:imports <https://example.org/external> ."
    ), format="turtle")
    metric = analyze_graph(graph)
    assert metric["ontology_metadata_presence"] is False
    assert metric["documentation_literal_count"] == 0
    assert metric["class_label_coverage"] == ""

