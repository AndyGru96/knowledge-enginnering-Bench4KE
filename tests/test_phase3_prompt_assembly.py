from __future__ import annotations

import json
from pathlib import Path

from rdflib import Graph

from scripts.validate_phase3_prompts import (
    APPROVED_METHODS,
    FIXED_DATASET_ID,
    PREVIOUS_OUTPUT_SENTINEL,
    REPO_ROOT,
    SNAPSHOT_ROOT,
    _import_adapter,
    build_snapshot_bundle,
    capture_method,
    load_authoritative_prompts,
    load_frozen_item,
    sha256_text,
)


def test_golden_snapshots_match_actual_adapter_prompts() -> None:
    assembled, expected_metadata = build_snapshot_bundle()

    for method in APPROVED_METHODS:
        directory = SNAPSHOT_ROOT / method
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        assert metadata == expected_metadata[method]
        assert metadata["dataset_id"] == FIXED_DATASET_ID
        assert metadata["provider"] == "deterministic-recording-mock"
        assert metadata["real_provider_calls"] == 0
        assert metadata["final_output_parse_valid"] is True
        assert metadata["manual_review"]["status"] == "reviewed"

        for index, actual_prompt in enumerate(assembled[method], start=1):
            path = directory / f"{FIXED_DATASET_ID}_P0_step_{index:02d}.txt"
            assert path.read_text(encoding="utf-8") == actual_prompt
            assert metadata["calls"][index - 1]["assembled_prompt_sha256"] == (
                sha256_text(actual_prompt)
            )

        last = directory / f"{FIXED_DATASET_ID}_P0_step_last.txt"
        assert last.read_text(encoding="utf-8") == assembled[method][-1]
        assert metadata["step_last_sha256"] == sha256_text(assembled[method][-1])


def test_multistep_order_and_previous_output_propagation() -> None:
    assembled, metadata = build_snapshot_bundle()

    for method in ("ontogenia", "domain-ontogen"):
        assert metadata[method]["call_count"] == 5
        assert [call["cq_id"] for call in metadata[method]["calls"]] == [
            "SOURCECQ1",
            "SOURCECQ2",
            "SOURCECQ4",
            "SOURCECQ5",
            "SOURCECQ6",
        ]
        assert metadata[method]["validation"]["previous_output_policy"] == (
            "forbidden_independent_cq_calls"
        )

    assert metadata["neon-gpt"]["call_count"] == 2
    assert metadata["neon-gpt"]["call_order"] == [
        "initial_assembly",
        "syntax_check_repair",
    ]
    assert PREVIOUS_OUTPUT_SENTINEL not in assembled["neon-gpt"][0]
    assert PREVIOUS_OUTPUT_SENTINEL in assembled["neon-gpt"][1]
    assert "Stage: syntax_check" in assembled["neon-gpt"][1]


def test_one_item_three_method_mock_integration_saves_distinct_valid_outputs(
    tmp_path: Path,
) -> None:
    adapter = _import_adapter()
    item, _other = load_frozen_item()
    prompts, _sources = load_authoritative_prompts(adapter)
    saved_paths: list[Path] = []
    contents: list[str] = []

    for method in APPROVED_METHODS:
        response, recorder = capture_method(
            adapter,
            method,
            item,
            prompts[method],
            force_neon_repair=False,
        )
        expected_calls = 1 if method == "neon-gpt" else len(
            item["competency_questions"]
        )
        assert len(recorder.calls) == expected_calls
        assert response.metadata["system_name"] == method
        assert response.metadata["model"] == "phase3-deterministic-mock"

        graph = Graph()
        graph.parse(data=response.ontology.content, format="turtle")
        assert len(graph) > 0

        output_path = tmp_path / method / item["dataset_id"] / "final_ontology.ttl"
        output_path.parent.mkdir(parents=True)
        output_path.write_text(response.ontology.content, encoding="utf-8")
        assert output_path.is_file()
        assert output_path.read_text(encoding="utf-8") == response.ontology.content
        saved_paths.append(output_path)
        contents.append(response.ontology.content)

    assert len({path.resolve() for path in saved_paths}) == len(APPROVED_METHODS)
    assert len(set(contents)) == len(APPROVED_METHODS)
    assert all(str(path).startswith(str(tmp_path)) for path in saved_paths)


def test_phase3_does_not_select_variants_or_real_provider() -> None:
    source = (REPO_ROOT / "scripts" / "validate_phase3_prompts.py").read_text(
        encoding="utf-8"
    )
    assert APPROVED_METHODS == ("ontogenia", "domain-ontogen", "neon-gpt")
    assert "ontogenia-mp" not in APPROVED_METHODS
    assert "neon-gpt-llms4life" not in APPROVED_METHODS
    assert "Real OpenAI clients are forbidden in Phase 3" in source
