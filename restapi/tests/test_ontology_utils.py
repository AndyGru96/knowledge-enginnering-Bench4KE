import os
import sys

sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.utils.ontology_dataset import load_ontology_items
from app.utils.ontology_external_call import call_external_ontology_service


def test_load_ontology_items_from_dir(tmp_path):
    file_a = tmp_path / "a.jsonl"
    file_b = tmp_path / "b.jsonl"
    file_a.write_text(
        '{"id": 1, "competency_questions": ["Q1"], "prompt_template": "p1"}\n',
        encoding="utf-8",
    )
    file_b.write_text(
        '{"dataset_id": "b", "competency_questions": ["Q2"]}\n',
        encoding="utf-8",
    )

    items = load_ontology_items(str(tmp_path))
    assert len(items) == 2
    assert items[0].dataset_id == "1"
    assert items[0].metadata["prompt_template"] == "p1"
    assert items[1].dataset_id == "b"


def test_load_ontology_items_invalid_json(tmp_path):
    file_a = tmp_path / "a.jsonl"
    file_a.write_text("{bad json}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_ontology_items(str(tmp_path))


def test_load_ontology_items_missing_required_fields(tmp_path):
    file_a = tmp_path / "a.jsonl"
    file_a.write_text('{"dataset_id": "x"}\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_ontology_items(str(tmp_path))


def test_call_external_ontology_service_non_json(monkeypatch):
    class DummyResponse:
        status_code = 200
        text = "not json"

        def json(self):
            raise ValueError("nope")

    def stub_post(*args, **kwargs):
        return DummyResponse()

    monkeypatch.setattr("app.utils.ontology_external_call.requests.post", stub_post)

    with pytest.raises(Exception, match="Error reading response JSON"):
        call_external_ontology_service({}, "http://example.com", timeout=1)


def test_call_external_ontology_service_http_error(monkeypatch):
    class DummyResponse:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    def stub_post(*args, **kwargs):
        return DummyResponse()

    monkeypatch.setattr("app.utils.ontology_external_call.requests.post", stub_post)

    with pytest.raises(Exception, match="External ontology service error"):
        call_external_ontology_service({}, "http://example.com", timeout=1)
