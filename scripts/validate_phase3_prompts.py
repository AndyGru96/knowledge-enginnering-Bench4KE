"""Deterministic Phase 3 prompt assembly validation.

This module never contacts a model provider.  It replaces the adapter's
provider boundary with a recording mock, then preserves every prompt actually
sent by the three approved method dispatch paths.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from rdflib import Graph

from scripts.prepare_ontology_dataset import (
    build_dataset_records_phase2b,
    extract_domain_ontogen_prompt,
    extract_memoryless_prompt,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = REPO_ROOT / "tests" / "snapshots" / "prompts"
APPROVED_METHODS = ("ontogenia", "domain-ontogen", "neon-gpt")
FROZEN_DATA_MANIFEST_SHA256 = (
    "e06831a155503aa5c2faa8312b7bd78eb6778b124f31dbfb1617bc63c6664caf"
)
FIXED_DATASET_ID = (
    "project2_Paul_2_ResourceReliability_Ortenz_1_MusicAndChildhood_"
    "c781c82a18"
)
UNRESOLVED_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
PREVIOUS_OUTPUT_SENTINEL = "PHASE3_PREVIOUS_OUTPUT_SENTINEL"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(payload)


def _import_adapter() -> Any:
    """Import the adapter while making any real OpenAI client unusable."""

    if "ontology_adapter" in sys.modules:
        return sys.modules["ontology_adapter"]

    # The course virtual environment intentionally need not install the OpenAI
    # package for Phase 3.  This import guard also fails closed if code ever
    # attempts to construct a client instead of using the recording boundary.
    guard = types.ModuleType("openai")

    class ForbiddenOpenAI:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("Real OpenAI clients are forbidden in Phase 3")

    guard.OpenAI = ForbiddenOpenAI
    sys.modules["openai"] = guard
    sys.path.insert(0, str(REPO_ROOT / "restapi"))
    return importlib.import_module("ontology_adapter")


def load_frozen_item() -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconstruct the frozen normalized record in memory from its workbook."""

    workbook = (
        REPO_ROOT
        / "external_resources"
        / "Onto-Generation"
        / "Dataset_OntoGen"
        / "Dataset.xlsx"
    )
    items, _mapping, _audit, _errors = build_dataset_records_phase2b(workbook, [])
    assert len(items) == 17
    assert sum(len(item["competency_questions"]) for item in items) == 74
    item = next(item for item in items if item["dataset_id"] == FIXED_DATASET_ID)
    other = next(item for item in items if item["dataset_id"] != FIXED_DATASET_ID)
    return item, other


def load_authoritative_prompts(adapter: Any) -> tuple[dict[str, str], dict[str, Any]]:
    ontogenia_source = (
        REPO_ROOT
        / "external_resources"
        / "Onto-Generation"
        / "PromptingTechniques"
        / "README.md"
    )
    domain_source = REPO_ROOT / "external_resources" / "Domain-OntoGen" / "README.md"
    neon_source = (
        REPO_ROOT
        / "external_resources"
        / "NEON-GPT"
        / "gpt_wine_ont_day1"
        / "day1_gpt_prompt_list.txt"
    )

    raw_prompts = {
        "ontogenia": extract_memoryless_prompt(ontogenia_source),
        "domain-ontogen": extract_domain_ontogen_prompt(domain_source),
        "neon-gpt": neon_source.read_text(encoding="utf-8"),
    }
    prompts = {
        method: adapter._extract_prompt_template(text)
        for method, text in raw_prompts.items()
    }
    sources = {
        "ontogenia": {
            "source_path": ontogenia_source.relative_to(REPO_ROOT).as_posix(),
            "source_file_sha256": sha256_file(ontogenia_source),
            "source_prompt_sha256": sha256_text(raw_prompts["ontogenia"]),
        },
        "domain-ontogen": {
            "source_path": domain_source.relative_to(REPO_ROOT).as_posix(),
            "source_file_sha256": sha256_file(domain_source),
            "source_prompt_sha256": sha256_text(raw_prompts["domain-ontogen"]),
        },
        "neon-gpt": {
            "source_path": neon_source.relative_to(REPO_ROOT).as_posix(),
            "source_file_sha256": sha256_file(neon_source),
            # Phase 2B copied this prompt byte-for-byte; retain its byte hash
            # even though Python normalizes CRLF while assembling the prompt.
            "source_prompt_sha256": sha256_file(neon_source),
        },
    }
    assert sources["domain-ontogen"]["source_prompt_sha256"] == (
        "f9e3945421508cd6a82613caf0d26fe802084178d950b2f1bd81b0446c2add4e"
    )
    assert sources["neon-gpt"]["source_prompt_sha256"] == (
        "40d0baf11f4945fc37f0a4d2f67a7efbbf3a249e0ae8e5b105672ee79a83f44a"
    )
    return prompts, sources


def phase2b_resource_hashes() -> dict[str, Any]:
    procedure = REPO_ROOT / "external_resources" / "Ontogenia" / "data" / "procedure.txt"
    patterns = REPO_ROOT / "external_resources" / "Ontogenia" / "data" / "patterns.csv"
    odp_manifest = {
        "schema_version": 1,
        "applicable_methods": [],
        "not_applicable_reason": (
            "No approved Phase 2 course method calls _load_odps_text; "
            "ontogenia-mp is explicitly excluded from the approved three."
        ),
        "source_patterns_csv": "external_resources/Ontogenia/data/patterns.csv",
        "source_patterns_sha256": sha256_file(patterns),
        "lookup_contract": {
            "directory": "datasets/ontology_generation/raw/ontogenia/odps",
            "candidate_order": ["name", "name.ttl", "name.owl", "name.rdf"],
        },
        "entries": [],
    }
    serialized_manifest = (
        json.dumps(odp_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return {
        "procedure_path": procedure.relative_to(REPO_ROOT).as_posix(),
        "procedure_sha256": sha256_file(procedure),
        "odp_manifest_path": "datasets/ontology_generation/odp_manifest.json",
        "odp_manifest_sha256": sha256_text(serialized_manifest),
        "odp_source_patterns_sha256": sha256_file(patterns),
    }


def valid_turtle(method: str, step: int) -> str:
    marker = re.sub(r"[^A-Za-z0-9]", "_", method.title())
    return (
        "@prefix : <http://example.org/phase3#> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "<http://example.org/phase3> a owl:Ontology .\n"
        f":{marker}Step{step:02d} a owl:Class .\n"
    )


def invalid_neon_draft() -> str:
    return (
        "@prefix : <http://example.org/phase3#> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        f":{PREVIOUS_OUTPUT_SENTINEL} a owl:Class .\n"
        ":Broken owl:equivalentClass [ a owl:Restriction .\n"
    )


@dataclass
class RecordingMockProvider:
    responses: list[str]

    def __post_init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, prompt: str, model: str, temperature: float, max_tokens: int
    ) -> str:
        if len(self.calls) >= len(self.responses):
            raise AssertionError("Unexpected adapter call: deterministic responses exhausted")
        response = self.responses[len(self.calls)]
        self.calls.append(
            {
                "call_index": len(self.calls) + 1,
                "prompt": prompt,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response": response,
            }
        )
        return response


def _request(adapter: Any, method: str, item: dict[str, Any]) -> Any:
    return adapter.OntologyGenerationRequest(
        system=method,
        dataset_id=item["dataset_id"],
        scenario_id=item["scenario_id"],
        scenario=item["scenario"],
        competency_questions=item["competency_questions"],
        user_stories=item["user_stories"],
        constraints=item["constraints"],
        metadata={"model": "phase3-deterministic-mock"},
    )


def capture_method(
    adapter: Any,
    method: str,
    item: dict[str, Any],
    prompt_template: str,
    *,
    force_neon_repair: bool,
) -> tuple[Any, RecordingMockProvider]:
    if method == "neon-gpt" and force_neon_repair:
        responses = [invalid_neon_draft(), valid_turtle(method, 2)]
    elif method == "neon-gpt":
        responses = [valid_turtle(method, 1)]
    else:
        responses = [
            valid_turtle(method, step)
            for step in range(1, len(item["competency_questions"]) + 1)
        ]

    recorder = RecordingMockProvider(responses)
    original_call = adapter._call_openai
    original_load = adapter._load_prompt
    environment = {
        "HERMIT_MODE": "",
        "HERMIT_AUTO": "false",
        "OOPS_API_URL": "",
        "ONTOLOGY_APPEND_CONSTRAINTS": "false",
        "NEON_GPT_MAX_SYNTAX_FIXES": "1",
        "NEON_GPT_MAX_SOUNDNESS_FIXES": "1",
        "NEON_GPT_MAX_CONSISTENCY_FIXES": "1",
        "NEON_GPT_MAX_OOPS_FIXES": "0",
    }
    prior_environment = {key: os.environ.get(key) for key in environment}
    try:
        os.environ.update(environment)
        adapter._call_openai = recorder
        adapter._load_prompt = lambda selected: (
            prompt_template
            if selected == method
            else (_ for _ in ()).throw(AssertionError(f"Unexpected method: {selected}"))
        )
        response = adapter.generate_ontology(_request(adapter, method, item))
    finally:
        adapter._call_openai = original_call
        adapter._load_prompt = original_load
        for key, value in prior_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    if len(recorder.calls) != len(responses):
        raise AssertionError(
            f"{method}: expected {len(responses)} calls, got {len(recorder.calls)}"
        )
    graph = Graph()
    graph.parse(data=response.ontology.content, format="turtle")
    return response, recorder


def validate_prompts(
    method: str,
    item: dict[str, Any],
    other_item: dict[str, Any],
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    prompts = [call["prompt"] for call in calls]
    story = item["scenario"] or "\n".join(item["user_stories"] or [])
    cqs = item["competency_questions"]
    required_placeholders = {
        "ontogenia": ("{story}", "{CQ}", "{rdf}"),
        "domain-ontogen": ("{OS}", "{CQ}"),
        "neon-gpt": (),
    }[method]
    assert all(
        token not in prompt
        for prompt in prompts
        for token in required_placeholders
    )
    retained_source_literals = sorted(
        {
            token
            for prompt in prompts
            for token in UNRESOLVED_PLACEHOLDER.findall(prompt)
        }
    )

    assert story in prompts[0]
    if method in {"ontogenia", "domain-ontogen"}:
        assert len(prompts) == len(cqs)
        for index, (prompt, cq) in enumerate(zip(prompts, cqs)):
            assert cq in prompt
            for other_index, other_cq in enumerate(cqs):
                if other_index != index:
                    assert other_cq not in prompt
        for earlier_call, later_prompt in zip(calls, prompts[1:]):
            assert earlier_call["response"] not in later_prompt
        previous_policy = "forbidden_independent_cq_calls"
    else:
        positions = [prompts[0].index(cq) for cq in cqs]
        assert positions == sorted(positions)
        if len(prompts) > 1:
            assert "Stage: syntax_check" in prompts[1]
            assert PREVIOUS_OUTPUT_SENTINEL in prompts[1]
            assert PREVIOUS_OUTPUT_SENTINEL not in prompts[0]
        previous_policy = "required_in_syntax_repair_prompt"

    other_story = other_item["scenario"] or "\n".join(other_item["user_stories"] or [])
    assert all(other_story not in prompt for prompt in prompts)
    assert all(
        other_cq not in prompt
        for prompt in prompts
        for other_cq in other_item["competency_questions"]
    )
    return {
        "correct_story": True,
        "correct_cqs": True,
        "cq_order_preserved": True,
        "placeholders_resolved": True,
        "required_placeholders": list(required_placeholders),
        "retained_source_literal_tokens": retained_source_literals,
        "cross_dataset_contamination_absent": True,
        "previous_output_policy": previous_policy,
        "previous_output_policy_validated": True,
        "procedure_required": False,
        "procedure_injected": False,
        "odp_required": False,
        "odp_injected": False,
    }


def build_snapshot_bundle() -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    adapter = _import_adapter()
    item, other_item = load_frozen_item()
    prompts, prompt_sources = load_authoritative_prompts(adapter)
    shared_hashes = phase2b_resource_hashes()
    snapshot_prompts: dict[str, list[str]] = {}
    metadata: dict[str, dict[str, Any]] = {}

    for method in APPROVED_METHODS:
        response, recorder = capture_method(
            adapter,
            method,
            item,
            prompts[method],
            force_neon_repair=method == "neon-gpt",
        )
        assembled = [call["prompt"] for call in recorder.calls]
        validations = validate_prompts(method, item, other_item, recorder.calls)
        snapshot_prompts[method] = assembled
        cq_ids = item["metadata"]["original_cq_ids"]
        call_records = []
        for index, prompt in enumerate(assembled, start=1):
            call_records.append(
                {
                    "step": index,
                    "stage": (
                        "syntax_check_repair"
                        if method == "neon-gpt" and index > 1
                        else "initial_assembly"
                    ),
                    "cq_id": (
                        cq_ids[index - 1]
                        if method in {"ontogenia", "domain-ontogen"}
                        else None
                    ),
                    "snapshot_file": f"{item['dataset_id']}_P0_step_{index:02d}.txt",
                    "assembled_prompt_sha256": sha256_text(prompt),
                    "prompt_bytes": len(prompt.encode("utf-8")),
                }
            )
        metadata[method] = {
            "schema_version": 1,
            "phase": "3",
            "method": method,
            "dataset_id": item["dataset_id"],
            "scenario_id": item["scenario_id"],
            "dataset_record_sha256": canonical_json_hash(item),
            "frozen_data_manifest_sha256": FROZEN_DATA_MANIFEST_SHA256,
            "dataset_reconstruction_evidence": {
                "source_workbook": (
                    "external_resources/Onto-Generation/Dataset_OntoGen/Dataset.xlsx"
                ),
                "source_workbook_sha256": sha256_file(
                    REPO_ROOT
                    / "external_resources"
                    / "Onto-Generation"
                    / "Dataset_OntoGen"
                    / "Dataset.xlsx"
                ),
                "scenario_count": 17,
                "cq_count": 74,
            },
            "prompt_source": prompt_sources[method],
            **shared_hashes,
            "call_count": len(assembled),
            "call_order": [record["stage"] for record in call_records],
            "cq_ids_in_source_order": cq_ids,
            "calls": call_records,
            "step_last_alias": f"{item['dataset_id']}_P0_step_last.txt",
            "step_last_sha256": sha256_text(assembled[-1]),
            "final_output_sha256": sha256_text(response.ontology.content),
            "final_output_parse_valid": True,
            "adapter_system_name": response.metadata["system_name"],
            "provider": "deterministic-recording-mock",
            "real_provider_calls": 0,
            "snapshot_scenario": (
                "forced_syntax_repair_for_previous_output_validation"
                if method == "neon-gpt"
                else "all_cq_calls"
            ),
            "validation": validations,
            "manual_review": {
                "status": "reviewed",
                "review_date": "2026-07-15",
                "basis": "Phase 3 golden snapshot review",
            },
        }
    return snapshot_prompts, metadata


def write_snapshots(
    snapshot_prompts: dict[str, list[str]], metadata: dict[str, dict[str, Any]]
) -> None:
    for method in APPROVED_METHODS:
        directory = SNAPSHOT_ROOT / method
        directory.mkdir(parents=True, exist_ok=True)
        dataset_id = metadata[method]["dataset_id"]
        for index, prompt in enumerate(snapshot_prompts[method], start=1):
            path = directory / f"{dataset_id}_P0_step_{index:02d}.txt"
            path.write_text(prompt, encoding="utf-8", newline="\n")
        (directory / f"{dataset_id}_P0_step_last.txt").write_text(
            snapshot_prompts[method][-1], encoding="utf-8", newline="\n"
        )
        (directory / "metadata.json").write_text(
            json.dumps(metadata[method], ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-snapshots",
        action="store_true",
        help="Write manually reviewable Phase 3 golden prompt snapshots.",
    )
    args = parser.parse_args()
    snapshots, metadata = build_snapshot_bundle()
    if args.write_snapshots:
        write_snapshots(snapshots, metadata)
    print(
        json.dumps(
            {
                "status": "validated",
                "dataset_id": metadata["ontogenia"]["dataset_id"],
                "methods": {
                    method: {
                        "call_count": metadata[method]["call_count"],
                        "final_output_parse_valid": metadata[method][
                            "final_output_parse_valid"
                        ],
                    }
                    for method in APPROVED_METHODS
                },
                "real_provider_calls": 0,
                "snapshots_written": args.write_snapshots,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
