import json
from pathlib import Path

import requests

from restapi.ontology_adapter import run_benchmark
from restapi.app.models_ontology import BenchmarkRequest


import pytest

from restapi.app.models_ontology import DatasetItem
from restapi.app.utils.llm_clients import OllamaAdapter

TTL = (
    "@prefix : <https://custom.example.org/schema#> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
    ":A a owl:Class ;\n"
    '    skos:definition "A sufficiently detailed definition of this example class." .\n'
    ":p a owl:ObjectProperty .\n"
    ":p a owl:FunctionalProperty ;\n"
    '    rdfs:comment "Each item has at most one related composition record." .\n'
)


class RecordingSession:
    def __init__(self, content=TTL):
        self.content = content
        self.requests = []
        self.preflight_requests = []

    def request(self, method, url, *, json, timeout):
        if method == "GET":
            self.preflight_requests.append(url)
            from restapi.app.config import OLLAMA_MODEL
            data = {"version": "mock-version"} if url.endswith("/api/version") else {"models": [{"name": OLLAMA_MODEL, "digest": "mock-digest"}]}
            class PreflightResponse:
                status_code = 200
                text = ""
                def json(self):
                    return data
            return PreflightResponse()
        self.requests.append({"method": method, "url": url, "json": json, "timeout": timeout})
        content = self.content
        class Response:
            status_code = 200
            text = ""
            def json(self):
                return {"message": {"role": "assistant", "content": content}, "done": True,
                        "done_reason": "stop", "prompt_eval_count": 12, "eval_count": 20}
        return Response()


@pytest.fixture
def session():
    return RecordingSession()


@pytest.fixture
def client(session):
    return OllamaAdapter("http://mock.invalid", session=session, sleeper=lambda _: None)


@pytest.fixture
def item():
    return DatasetItem(dataset_id="case-1", scenario="A course ontology story.",
                       competency_questions=["What is an item?", "What relates two items?"])


class EvidenceSession(RecordingSession):
    def __init__(self, data=None, disconnect=False):
        super().__init__()
        self.data = data
        self.disconnect = disconnect

    def request(self, method, url, **kwargs):
        if method == "GET":
            return super().request(method, url, **kwargs)
        if self.disconnect:
            raise requests.ConnectionError("Simulated connection failure")
        data = self.data
        class Response:
            status_code = 200
            content = json.dumps(data).encode("utf-8")
            text = content.decode("utf-8")
            def json(self):
                return data
        return Response()


@pytest.mark.parametrize("data,category", [
    ({"message": {"content": ""}, "done": True}, "empty_response"),
    ({"message": {"content": TTL}, "done": False}, "incomplete_response"),
    ({"message": {"content": TTL}, "done": True, "done_reason": "length"}, "incomplete_response"),
    ({"message": {"content": 42}, "done": True}, "malformed_response"),
])
def test_rejected_response_artifacts(tmp_path, item, data, category):
    item.competency_questions = ["One question?"]
    client = OllamaAdapter("http://mock.invalid", session=EvidenceSession(data), sleeper=lambda _: None)
    result = run_benchmark(BenchmarkRequest(system="ontogenia", items=[item]), client, tmp_path)
    call = Path(result["run_dir"]) / "tasks/task_0001/calls/call_01"
    assert json.loads((call / "response.json").read_text(encoding="utf-8")) == data
    assert json.loads((call / "http/attempt_01/body.bin").read_bytes()) == data
    error = json.loads((call / "error.json").read_text(encoding="utf-8"))
    assert error["category"] == category and error["response_received"] is True
    assert result["results"][0]["result_available"] is False
    assert result["results"][0]["triples"] is None
    if isinstance(data["message"]["content"], str):
        assert (call / "output.txt").read_bytes() == data["message"]["content"].encode("utf-8")


def test_connection_failure_artifacts(tmp_path, item):
    client = OllamaAdapter("http://mock.invalid", session=EvidenceSession(disconnect=True), sleeper=lambda _: None)
    result = run_benchmark(BenchmarkRequest(system="ontogenia", items=[item]), client, tmp_path)
    call = Path(result["run_dir"]) / "tasks/task_0001/calls/call_01"
    error = json.loads((call / "error.json").read_text(encoding="utf-8"))
    assert error["response_received"] is False and error["attempts"] == 3
    assert not (call / "response.json").exists() and not (call / "http").exists()


@pytest.mark.parametrize("status,body,attempts", [
    (200, b"invalid-json", 1),
    (400, b'{"error":"bad request"}', 1),
    (503, b'{"error":"temporarily unavailable"}', 3),
])
def test_http_error_body_preservation(tmp_path, item, status, body, attempts):
    class ErrorSession(RecordingSession):
        def request(self, method, url, **kwargs):
            if method == "GET":
                return super().request(method, url, **kwargs)
            class Response:
                status_code = status
                content = body
                text = body.decode("utf-8")
                def json(self):
                    return json.loads(body)
            return Response()
    client = OllamaAdapter("http://mock.invalid", session=ErrorSession(), sleeper=lambda _: None)
    result = run_benchmark(BenchmarkRequest(system="ontogenia", items=[item]), client, tmp_path)
    call = Path(result["run_dir"]) / "tasks/task_0001/calls/call_01"
    bodies = sorted((call / "http").glob("*/body.bin"))
    assert len(bodies) == attempts and all(p.read_bytes() == body for p in bodies)
    error = json.loads((call / "error.json").read_text(encoding="utf-8"))
    assert error["response_received"] is True
    if status != 200:
        assert json.loads((call / "response.json").read_text(encoding="utf-8")) == json.loads(body)

