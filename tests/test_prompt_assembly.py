import hashlib
import json
from pathlib import Path

import pytest
from rdflib import OWL, RDF, URIRef

from restapi.ontology_adapter import METHODS, load_template, run_benchmark
from restapi.app.models_ontology import BenchmarkRequest
from restapi.app.utils.llm_clients import OllamaAdapter, ProviderError
from restapi.app.utils.ontology_artifacts import parse_turtle
from scripts.analyze_documentation_completeness import analyze_graph
from scripts.analyze_prompt_sensitivity import analyze_run
from test_provider_artifacts import RecordingSession, TTL, item, client, session


def test_method_variant_requests(tmp_path, item, client, session):
    request = BenchmarkRequest(items=[item], prompt_variants=["P0", "P1", "P2"])
    result = run_benchmark(request, client, tmp_path)
    assert result["tasks"] == 9
    assert len(session.requests) == 42
    assert {r["method"] for r in result["results"]} == set(METHODS)
    for method in METHODS:
        hashes = {r["prompt_template_sha256"] for r in result["results"] if r["method"] == method}
        assert len(hashes) == 3
    for recorded in session.requests:
        payload = recorded["json"]
        assert payload["options"] == {"temperature": 0.0, "num_predict": 8192, "seed": 42, "num_ctx": 32768}
        assert payload["stream"] is False
    root = Path(result["run_dir"])
    stored = [
        json.loads(file.read_text(encoding="utf-8"))
        for file in sorted(root.glob("tasks/*/calls/*/request.json"))
    ]
    assert stored == [r["json"] for r in session.requests]
    for row in result["results"]:
        template = (root / "tasks" / row["task_id"] / "template.txt").read_bytes()
        assert hashlib.sha256(template).hexdigest() == row["prompt_template_sha256"]
    prompts = [r["json"]["messages"][0]["content"] for r in session.requests]
    assert all(item.scenario in prompt for prompt in prompts)
    assert any("silently verify" in r["json"]["messages"][-1]["content"] for r in session.requests)


@pytest.mark.parametrize("method", METHODS)
def test_ontology_output_preservation(tmp_path, item, client, method):
    item.competency_questions = ["What is an item?"]
    result = run_benchmark(BenchmarkRequest(system=method, items=[item]), client, tmp_path)
    row = result["results"][0]
    root = Path(result["run_dir"])
    expected = "\n\n".join([TTL] * (6 if method == "neon-gpt" else 1))
    assert (root / row["transcript_path"]).read_bytes() == expected.encode("utf-8")
    assert (root / "tasks/task_0001/calls/call_01/output.txt").read_bytes() == TTL.encode("utf-8")
    graph, error = parse_turtle(TTL)
    assert error is None
    assert URIRef("https://custom.example.org/schema#A") in graph.subjects(RDF.type, OWL.Class)
    assert len(set(graph.subjects(RDF.type, OWL.FunctionalProperty))) == 1
    assert analyze_graph(graph)["class_comment_definition_coverage"] == 1
    assert analyze_graph(graph)["object_property_comment_definition_coverage"] == 1
    assert row["parse_success"] is True


def test_fenced_output_is_not_repaired(tmp_path, item):
    fence = chr(96) * 3
    raw = " \r\n" + fence + "turtle\r\n" + TTL + fence + "\r\n"
    session = RecordingSession(raw)
    client = OllamaAdapter("http://mock.invalid", session=session)
    result = run_benchmark(BenchmarkRequest(system="neon-gpt", items=[item]), client, tmp_path)
    row = result["results"][0]
    assert row["result_available"] is True
    assert row["parse_success"] is False
    assert len(session.requests) == 10
    assert (Path(result["run_dir"]) / row["transcript_path"]).read_bytes() == "\n\n".join([raw] * 6).encode("utf-8")


def test_failed_generation_has_no_metrics(tmp_path, item):
    class FailedClient:
        def preflight(self, *_args, **_kwargs):
            return {"model_available": True}
        def chat_completion(self, *_args, **_kwargs):
            raise ProviderError("model_not_found", "Model is unavailable")
    result = run_benchmark(BenchmarkRequest(system="ontogenia", items=[item]), FailedClient(), tmp_path)
    row = result["results"][0]
    assert row["result_available"] is False
    assert row["parse_success"] is False
    assert row["triples"] is None
    assert row["successful_calls"] == 0


def test_neon_conversation_flow(tmp_path, item):
    class StepSession(RecordingSession):
        def request(self, method, url, **kwargs):
            if method != "GET":
                self.content = "Requirements and conceptual discussion" if len(self.requests) < 4 else TTL
            return super().request(method, url, **kwargs)
    session = StepSession()
    client = OllamaAdapter("http://mock.invalid", session=session)
    result = run_benchmark(BenchmarkRequest(system="neon-gpt", items=[item]), client, tmp_path)
    row = result["results"][0]
    assert row["successful_calls"] == 10 and row["parse_success"] is True
    assert len(json.loads(row["documents_json"])) == 6
    assert [len(r["json"]["messages"]) for r in session.requests] == list(range(1, 20, 2))
    assert all("wine" not in r["json"]["messages"][-1]["content"].lower() for r in session.requests)
    assert len(session.preflight_requests) == 2
    preflight = json.loads((Path(result["run_dir"]) / "preflight.json").read_text(encoding="utf-8"))
    assert preflight["version"] == "mock-version" and preflight["model_digest"] == "mock-digest"
    assert analyze_run(Path(result["run_dir"]), tmp_path / "analysis")["parseable_tasks"] == 1


def test_ontogenia_output_protocol():
    for variant in ("P0", "P1", "P2"):
        template, _ = load_template("ontogenia", variant)
        assert "write this text" in template  # Original source instruction is retained.
        assert "Turtle comments beginning with #" in template

