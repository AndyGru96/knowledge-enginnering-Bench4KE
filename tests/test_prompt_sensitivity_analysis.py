import csv
import json
from pathlib import Path

import pytest
from rdflib import Graph, OWL, RDF, URIRef

from restapi.ontology_adapter import run_benchmark
from restapi.app.models_ontology import BenchmarkRequest
from restapi.app.utils.llm_clients import OllamaAdapter
from restapi.app.utils.ontology_artifacts import load_task_graph, parse_documents, parse_turtle, read_csv, scenario_base, write_csv
from scripts.analyze_documentation_completeness import analyze_ontology
from scripts.analyze_prompt_sensitivity import analyze, analyze_run, build_term_jaccard_rows, cochran_q, holm_adjust, jaccard, mcnemar_rows, ontology_terms, wilcoxon_rows
from test_provider_artifacts import RecordingSession, item, client, session


def test_exact_iri_comparison(tmp_path):
    path = tmp_path / "ontology_transcript.txt"
    content = (
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "<https://Example.org:443/A%62> a owl:Class .\n"
        "<https://example.org/Ab> a owl:Class ."
    )
    path.write_bytes(content.encode("utf-8"))
    terms, error = ontology_terms(path)
    assert error is None
    assert terms == {"https://Example.org:443/A%62", "https://example.org/Ab"}
    assert path.read_bytes() == content.encode("utf-8")
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_degenerate_samples_and_holm():
    assert cochran_q([[1, 1, 1], [1, 1, 1]])["statistic"] is None
    assert cochran_q([[1, 0, 0], [1, 0, 0], [0, 1, 0]])["statistic"] == pytest.approx(2)
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert wilcoxon_rows([]) == []


def test_analysis_preserves_run_outputs(tmp_path, item, client):
    result = run_benchmark(BenchmarkRequest(items=[item], prompt_variants=["P0", "P1", "P2"]), client, tmp_path / "runs")
    root = Path(result["run_dir"])
    originals = {p: p.read_bytes() for p in [*root.glob("tasks/*/ontology_transcript.txt"), *root.glob("tasks/*/calls/*/output.txt")]}
    assert originals
    summary = analyze_run(root, tmp_path / "analysis")
    assert summary["tasks"] == summary["parseable_tasks"] == 9
    with (tmp_path / "analysis/c2_documentation_completeness.csv").open(encoding="utf-8") as handle:
        detail = list(csv.DictReader(handle))
    assert len(detail) == 9
    assert all(row["metric_status"] == "available" for row in detail)
    with (tmp_path / "analysis/c3_term_jaccard.csv").open(encoding="utf-8") as handle:
        terms = list(csv.DictReader(handle))
    assert len(terms) == 3
    assert all(float(row["J_P0_P1"]) == float(row["J_P0_P2"]) == 1 for row in terms)
    assert all(path.read_bytes() == content for path, content in originals.items())


def test_relative_and_explicit_base_iris(tmp_path):
    text = '@prefix owl: <http://www.w3.org/2002/07/owl#> . <#A> a owl:Class .'
    paths = [tmp_path / "P0/a.ttl", tmp_path / "P1/b.ttl"]
    for path in paths:
        path.parent.mkdir()
        path.write_bytes(text.encode("utf-8"))
    assert jaccard(ontology_terms(paths[0])[0], ontology_terms(paths[1])[0]) == 1
    assert analyze_ontology(paths[0]) == analyze_ontology(paths[1])
    explicit = '@base <https://explicit.example/model/> .\n' + text
    graph, error = parse_turtle(explicit, scenario_base("case"))
    assert error is None
    assert URIRef("https://explicit.example/model/#A") in graph.subjects(RDF.type, OWL.Class)
    assert all(path.read_bytes() == text.encode("utf-8") for path in paths)


def test_blank_node_scope_isolation(tmp_path, item):
    content = '@prefix owl: <http://www.w3.org/2002/07/owl#> . _:r a owl:Restriction .'
    # Failing control: text concatenation collapses the document-scoped label.
    control = Graph().parse(data=content + "\n" + content, format="turtle")
    assert len(set(control.subjects(RDF.type, OWL.Restriction))) == 1
    graph, error = parse_documents([content, content], scenario_base(item.dataset_id))
    assert error is None
    assert len(set(graph.subjects(RDF.type, OWL.Restriction))) == 2
    client = OllamaAdapter("http://mock.invalid", session=RecordingSession(content))
    result = run_benchmark(BenchmarkRequest(system="ontogenia", items=[item]), client, tmp_path)
    row = result["results"][0]
    graph, _ = load_task_graph(row, Path(result["run_dir"]))
    assert row["triples"] == len(graph) == 2
    assert len(set(graph.subjects(RDF.type, OWL.Restriction))) == 2
    assert analyze_run(Path(result["run_dir"]), tmp_path / "analysis")["parseable_tasks"] == 1


def test_mcnemar_with_two_variants(tmp_path):
    panel = {str(i): {"P0": 1, "P1": 0} for i in range(8)}
    row = mcnemar_rows("ontogenia", panel)[0]
    assert row["paired_n"] == 8 and row["status"] == "estimated"
    assert row["p_value_raw"] == pytest.approx(2 / 256)
    path = tmp_path / "panel.csv"
    write_csv(path, [{"method": "ontogenia", "dataset_id": key, "prompt_variant": v,
                      "result_available": True, "parse_success": bool(value)}
                     for key, values in panel.items() for v, value in values.items()])
    result = analyze(path, [])
    assert result["cochran"][0]["status"] == "not_estimable_three_variants_not_selected"
    assert len(result["mcnemar"]) == 1
    assert result["mcnemar"][0]["paired_n"] == 8
    assert mcnemar_rows("ontogenia", {"a": {"P0": 1, "P1": 1}})[0]["p_value_raw"] == 1


def test_p1_p2_comparison_without_p0(tmp_path, item, client):
    result = run_benchmark(BenchmarkRequest(system="domain-ontogen", items=[item], prompt_variants=["P1", "P2"]), client, tmp_path / "runs")
    root = Path(result["run_dir"])
    terms = build_term_jaccard_rows(read_csv(root / "task_index.csv"), root)
    assert terms[0]["J_P1_P2"] == 1
    assert terms[0]["J_P1_P2_status"] == "available"
    assert terms[0]["J_P0_P1"] is None
    assert terms[0]["included_in_wilcoxon"] is False
    assert analyze_run(root, tmp_path / "analysis")["tasks"] == 2


@pytest.mark.parametrize("tamper", ["unknown_dataset", "duplicate", "wrong_output", "request_setting", "dataset_snapshot"])
def test_analysis_rejects_inconsistent_records(tmp_path, item, client, tamper):
    result = run_benchmark(BenchmarkRequest(system="ontogenia", items=[item], prompt_variants=["P0", "P1"]), client, tmp_path / "runs")
    root = Path(result["run_dir"])
    index = root / "task_index.csv"
    rows = read_csv(index)
    if tamper == "unknown_dataset":
        rows[0]["dataset_id"] = "does-not-exist"
        write_csv(index, rows)
    elif tamper == "duplicate":
        rows[0]["prompt_variant"] = rows[1]["prompt_variant"]
        write_csv(index, rows)
    elif tamper == "wrong_output":
        rows[0]["transcript_path"] = rows[1]["transcript_path"]
        write_csv(index, rows)
    elif tamper == "request_setting":
        path = root / "tasks/task_0001/calls/call_01/request.json"
        request = json.loads(path.read_text(encoding="utf-8"))
        request["options"]["temperature"] = 7
        path.write_text(json.dumps(request), encoding="utf-8")
    else:
        path = root / "dataset.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data[0]["scenario"] = "Different scene"
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        analyze_run(root, tmp_path / "analysis")


def test_interrupted_run_partial_analysis(tmp_path, item):
    item.competency_questions = ["One question?"]
    second = item.model_copy(update={"dataset_id": "case-2"})
    class InterruptedClient(OllamaAdapter):
        count = 0
        def chat_completion(self, *args, **kwargs):
            self.count += 1
            if self.count == 2:
                raise KeyboardInterrupt()
            return super().chat_completion(*args, **kwargs)
    client = InterruptedClient("http://mock.invalid", session=RecordingSession())
    with pytest.raises(KeyboardInterrupt):
        run_benchmark(BenchmarkRequest(system="ontogenia", items=[item, second]), client, tmp_path / "runs")
    root = next((tmp_path / "runs").iterdir())
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "interrupted" and manifest["completed_tasks"] == 1
    assert len(json.loads((root / "plan.json").read_text(encoding="utf-8"))) == 2
    rows = read_csv(root / "task_index.csv")
    assert [row["status"] for row in rows] == ["completed", "running"]
    with pytest.raises(ValueError, match="incomplete"):
        analyze_run(root, tmp_path / "analysis")
    summary = analyze_run(root, tmp_path / "partial", allow_partial=True)
    assert summary["unfinished_tasks"] == 1 and summary["parseable_tasks"] == 1


def test_empty_ontology_stability_exclusion(tmp_path, item):
    client = OllamaAdapter("http://mock.invalid", session=RecordingSession("# Only comments\n"))
    result = run_benchmark(BenchmarkRequest(system="ontogenia", items=[item], prompt_variants=["P0", "P1", "P2"]), client, tmp_path / "runs")
    assert all(row["parse_success"] and row["empty_ontology"] and not row["has_evaluable_entities"] for row in result["results"])
    root = Path(result["run_dir"])
    terms = build_term_jaccard_rows(read_csv(root / "task_index.csv"), root)
    assert terms[0]["J_P0_P1"] == 1
    assert terms[0]["J_P0_P1_status"] == "unavailable_or_empty_terms"
    assert terms[0]["included_in_wilcoxon"] is False
    assert analyze_run(root, tmp_path / "analysis")["empty_ontology_tasks"] == 3

