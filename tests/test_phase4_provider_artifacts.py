from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from restapi.app.utils.llm_clients import LLMResponse
from restapi.app.utils.ontology_artifacts import (
    cache_identity,
    result_state,
    should_execute,
    write_result_envelope,
)
from scripts.validate_phase3_prompts import (
    APPROVED_METHODS,
    _import_adapter,
    load_authoritative_prompts,
    load_frozen_item,
)


VALID_TTL = (
    "@prefix : <http://example.org/phase4#> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    ":Phase4Class a owl:Class .\n"
)


class FakeProvider:
    provider_name = "ollama"

    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, options):
        self.calls.append((messages, options))
        return LLMResponse(
            content=VALID_TTL,
            raw_response={
                "model": options.model,
                "message": {"content": VALID_TTL},
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 8,
            },
            telemetry={
                "model": options.model,
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 8,
            },
            provider="ollama",
        )


@pytest.mark.parametrize(
    "method,expected_calls", [("ontogenia", 5), ("domain-ontogen", 5), ("neon-gpt", 1)]
)
def test_all_approved_methods_dispatch_through_common_provider(
    monkeypatch, method, expected_calls
):
    adapter = _import_adapter()
    item, _other = load_frozen_item()
    prompts, _sources = load_authoritative_prompts(adapter)
    provider = FakeProvider()
    monkeypatch.setattr(adapter, "get_llm_client", lambda *_args, **_kwargs: provider)
    monkeypatch.setattr(adapter, "_load_prompt", lambda selected: prompts[selected])
    monkeypatch.setenv("HERMIT_MODE", "")
    monkeypatch.setenv("HERMIT_AUTO", "false")
    monkeypatch.setenv("OOPS_API_URL", "")

    request = adapter.OntologyGenerationRequest(
        system=method,
        dataset_id=item["dataset_id"],
        scenario_id=item["scenario_id"],
        scenario=item["scenario"],
        competency_questions=item["competency_questions"],
        user_stories=item["user_stories"],
        constraints=item["constraints"],
        metadata={
            "provider": "ollama",
            "model": "phase4-mock",
            "temperature": 0,
            "seed": 42,
            "num_ctx": 8192,
            "max_output_tokens": 4096,
            "timeout_seconds": 1800,
            "keep_alive": "30m",
        },
    )
    response = adapter.generate_ontology(request)
    assert len(provider.calls) == expected_calls
    assert response.metadata["provider"] == "ollama"
    assert response.metadata["internal_call_count"] == expected_calls
    assert len(response.metadata["internal_calls"]) == expected_calls
    assert response.metadata["internal_calls"][0]["telemetry"]["eval_count"] == 8
    assert response.metadata["seed"] == 42
    assert response.metadata["num_ctx"] == 8192
    assert response.metadata["keep_alive"] == "30m"
    assert response.metadata["stream"] is False


def base_cache_values():
    return {
        "dataset_id": "dataset-1",
        "method": "ontogenia",
        "provider": "ollama",
        "model": "model",
        "model_digest": "digest",
        "prompt_hash": "prompt",
        "temperature": 0,
        "seed": 42,
        "num_ctx": 8192,
        "max_output_tokens": 4096,
        "procedure_hash": None,
        "odp_manifest_hash": "odp",
        "repair_policy": "approved",
        "repository_commit": "commit",
        "dataset_manifest_hash": "manifest",
        "experiment_config_hash": None,
    }


def test_cache_identity_invalidates_prompt_seed_and_context_but_not_keep_alive():
    base = base_cache_values()
    original, _ = cache_identity(base)
    for key, value in (("prompt_hash", "other"), ("seed", 7), ("num_ctx", 16384)):
        changed = dict(base)
        changed[key] = value
        assert cache_identity(changed)[0] != original
    keep_alive = dict(base, keep_alive="0")
    assert cache_identity(keep_alive)[0] == original


def test_output_ledger_separates_raw_normalized_final_and_is_atomic(tmp_path):
    raw_invalid = "@prefix : <http://example.org/> .\n:Broken ["
    metadata = {
        "provider": "ollama",
        "model": "model",
        "temperature": 0,
        "seed": 42,
        "num_ctx": 8192,
        "max_output_tokens": 4096,
        "timeout_seconds": 1800,
        "keep_alive": "30m",
        "normalized_output": VALID_TTL,
        "internal_calls": [
            {
                "call_index": 1,
                "assembled_prompt": "prompt one",
                "request": {"model": "model", "messages": [{"role": "user", "content": "prompt one"}]},
                "raw_response": {"message": {"content": raw_invalid}},
                "raw_output": raw_invalid,
                "normalized_output": VALID_TTL,
                "final_ontology": VALID_TTL,
                "previous_output_input": "",
                "telemetry": {"eval_count": 3},
                "attempts": 1,
            },
            {
                "call_index": 2,
                "assembled_prompt": "repair prompt",
                "raw_response": {"message": {"content": VALID_TTL}},
                "raw_output": VALID_TTL,
                "telemetry": {"eval_count": 4},
                "attempts": 1,
            },
        ],
        "repair_attempt_count": 1,
    }
    directory = write_result_envelope(
        tmp_path,
        dataset_id="dataset-1",
        method="neon-gpt",
        model="model",
        prompt_variant="P0",
        seed=42,
        final_ontology=VALID_TTL,
        adapter_metadata=metadata,
        generation_metadata=base_cache_values(),
    )
    parse = json.loads((directory / "parse_metadata.json").read_text())
    assert parse["raw_parse_success"] is False
    assert parse["normalized_parse_success"] is True
    assert parse["final_parse_success"] is True
    assert parse["llm_repair_used"] is True
    assert (directory / "raw_output.txt").read_text() != (
        directory / "final_ontology.ttl"
    ).read_text()
    assert (directory / "steps/step_01/assembled_prompt.txt").read_text() == "prompt one"
    assert json.loads((directory / "steps/step_01/request.json").read_text())[
        "messages"
    ][0]["content"] == "prompt one"
    assert (directory / "steps/step_02/raw_output.txt").read_text() == VALID_TTL
    assert (directory / "steps/step_01/normalized_output.txt").read_text() == VALID_TTL
    assert (directory / "steps/step_01/final_ontology.ttl").read_text() == VALID_TTL
    assert json.loads(
        (directory / "steps/step_01/parse_metadata.json").read_text()
    )["final_parse_success"] is True
    assert (directory / "steps/step_01/previous_output_input.txt").read_text() == ""
    assert json.loads((directory / "steps/step_01/step_metadata.json").read_text())[
        "assembled_prompt_sha256"
    ] == hashlib.sha256(b"prompt one").hexdigest()
    assert not list(directory.rglob(".*.tmp"))
    generation = json.loads((directory / "generation_metadata.json").read_text())
    assert generation["output_contract_version"] == "project2-result-envelope-v2"
    assert generation["provider_telemetry"][1]["eval_count"] == 4
    assert result_state(directory, generation["cache_key"]) == "success"

    (directory / "steps/step_02/raw_response.json").unlink()
    assert result_state(directory, generation["cache_key"]) == "incomplete"


def test_resume_and_retry_failed_selection():
    assert should_execute("success", resume=True, retry_failed=False) is False
    assert should_execute("stale", resume=True, retry_failed=False) is True
    assert should_execute("failed", resume=True, retry_failed=True) is True
    assert should_execute("success", resume=True, retry_failed=True) is False


def test_failed_result_is_archived_before_retry_and_raw_is_preserved(tmp_path):
    failed_metadata = {
        "provider": "ollama",
        "model": "model",
        "internal_calls": [
            {
                "assembled_prompt": "first prompt",
                "raw_response": {"error": "failed"},
                "raw_output": "first raw output",
            }
        ],
    }
    failed_dir = write_result_envelope(
        tmp_path,
        dataset_id="dataset-archive",
        method="ontogenia",
        model="model",
        prompt_variant="P0",
        seed=42,
        final_ontology="",
        adapter_metadata=failed_metadata,
        generation_metadata=base_cache_values(),
        error={"error": "failed"},
    )
    assert result_state(failed_dir) == "failed"

    success_dir = write_result_envelope(
        tmp_path,
        dataset_id="dataset-archive",
        method="ontogenia",
        model="model",
        prompt_variant="P0",
        seed=42,
        final_ontology=VALID_TTL,
        adapter_metadata={
            **failed_metadata,
            "normalized_output": VALID_TTL,
            "internal_calls": [
                {
                    "assembled_prompt": "retry prompt",
                    "raw_response": {"message": {"content": VALID_TTL}},
                    "raw_output": VALID_TTL,
                }
            ],
        },
        generation_metadata=base_cache_values(),
    )
    archives = list(success_dir.parent.glob("seed_42__archive_*"))
    assert len(archives) == 1
    assert (archives[0] / "raw_output.txt").read_text() == "first raw output"
    assert (success_dir / "raw_output.txt").read_text() == VALID_TTL


def test_runner_preserves_adapter_metadata_and_contract(monkeypatch, tmp_path):
    restapi_path = str(Path(__file__).resolve().parents[1] / "restapi")
    if restapi_path not in sys.path:
        sys.path.insert(0, restapi_path)
    import app.routers.ontology_benchmark as benchmark

    app = FastAPI()
    app.include_router(benchmark.router, prefix="/ontology")

    adapter_metadata = {
        "system_name": "ontogenia",
        "model": "phase4-mock",
        "provider": "ollama",
        "temperature": 0,
        "seed": 42,
        "num_ctx": 8192,
        "max_output_tokens": 4096,
        "timeout_seconds": 1800,
        "keep_alive": "30m",
        "normalized_output": VALID_TTL,
        "internal_calls": [
            {
                "call_index": 1,
                "assembled_prompt": "assembled",
                "raw_response": {"message": {"content": VALID_TTL}},
                "raw_output": VALID_TTL,
                "telemetry": {"prompt_eval_count": 7, "eval_count": 5},
                "attempts": 1,
            }
        ],
    }
    monkeypatch.setattr(
        benchmark,
        "call_external_ontology_service",
        lambda *_args, **_kwargs: {
            "ontology": {"format": "ttl", "content": VALID_TTL},
            "metadata": adapter_metadata,
        },
    )
    monkeypatch.setattr(benchmark, "ONTOLOGY_RUNS_DIR", str(tmp_path / "runs"))
    response = TestClient(app).post(
        "/ontology/run",
        json={
            "items": [
                {
                    "dataset_id": "dataset-runner",
                    "system": "ontogenia",
                    "competency_questions": ["CQ"],
                    "metadata": {
                        "model_digest": "sha256:phase4",
                        "experiment_config_hash": "config-hash",
                        "Ollama_version": "0.test",
                        "source_prompt_path": "prompts/ontogenia/P0_original.txt",
                    },
                }
            ],
            "provider": "ollama",
            "model": "phase4-mock",
            "evaluation_mode": "none",
            "resume": False,
            "save_results": True,
        },
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["adapter_metadata"]["internal_calls"][0]["telemetry"]["eval_count"] == 5
    assert Path(result["generation_metadata_file"]).is_file()
    assert Path(result["parse_metadata_file"]).is_file()
    assert Path(result["result_dir"], "raw_response.json").is_file()
    assert Path(result["ontology_file"]).read_text() == VALID_TTL
    generation = json.loads(Path(result["generation_metadata_file"]).read_text())
    assert generation["cache_identity_schema_version"] == 2
    assert generation["model_digest"] == "sha256:phase4"
    assert generation["experiment_config_hash"] == "config-hash"
    assert generation["Ollama_version"] == "0.test"
    assert generation["source_prompt_path"] == "prompts/ontogenia/P0_original.txt"
    assert generation["prompt_characters"] == len("assembled")
    assert generation["raw_output_characters"] == len(VALID_TTL)
    assert generation["final_output_characters"] == len(VALID_TTL)
    assert generation["prompt_tokens"] == 7
    assert generation["completion_tokens"] == 5
    assert generation["wall_clock_seconds"] >= 0
