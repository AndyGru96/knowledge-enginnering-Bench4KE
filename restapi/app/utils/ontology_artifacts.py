"""Atomic Project 2 result envelopes, parse ledger and semantic cache identity."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from rdflib import Graph


REQUIRED_RESULT_FILES = (
    "assembled_prompt.txt",
    "raw_response.json",
    "raw_output.txt",
    "normalized_output.txt",
    "final_ontology.ttl",
    "generation_metadata.json",
    "parse_metadata.json",
    "error.json",
    "status.json",
)
OUTPUT_CONTRACT_VERSION = "project2-result-envelope-v2"
REQUIRED_STEP_FILES = (
    "assembled_prompt.txt",
    "request.json",
    "raw_response.json",
    "raw_output.txt",
    "normalized_output.txt",
    "final_ontology.ttl",
    "parse_metadata.json",
    "step_metadata.json",
)
CACHE_FIELDS = (
    "dataset_id",
    "method",
    "provider",
    "model",
    "model_digest",
    "prompt_hash",
    "temperature",
    "seed",
    "num_ctx",
    "max_output_tokens",
    "procedure_hash",
    "odp_manifest_hash",
    "repair_policy",
    "repository_commit",
    "dataset_manifest_hash",
    "experiment_config_hash",
)
CACHE_IDENTITY_SCHEMA_V1 = 1
CACHE_IDENTITY_SCHEMA_V2 = 2
CACHE_SCHEMA_FIELD = "cache_identity_schema_version"
A2_CACHE_IDENTITY_CONTRACT = "stage-a2-v1"
A2_CACHE_FIELDS = (
    "cache_identity_contract",
    "dataset_id",
    "dataset_record_hash",
    "method",
    "prompt_variant",
    "provider",
    "model",
    "model_digest",
    "prompt_hash",
    "temperature",
    "seed",
    "num_ctx",
    "max_output_tokens",
    "procedure_hash",
    "odp_manifest_hash",
    "repair_policy",
    "normalization_policy",
    "parser_output_contract_version",
    "repository_commit",
    "code_identity_hash",
    "dataset_manifest_hash",
    "experiment_config_hash",
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def cache_identity_v1(values: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Reproduce the historical schema-v1 key byte-for-byte.

    Schema v1 intentionally preserves Python's JSON distinction between an
    integer and an integral float.  It remains available only for historical
    verification and must not be used to admit a legacy result as a v2 hit.
    """
    identity = {field: values.get(field) for field in CACHE_FIELDS}
    payload = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), identity


def cache_identity(values: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Backward-compatible alias for historical schema-v1 reproduction."""
    return cache_identity_v1(values)


def _canonical_json_v2(value: Any) -> str:
    """Return the schema-v2 deterministic JSON representation.

    Booleans are handled before integers because ``bool`` subclasses ``int``.
    Finite floats that are mathematically integral are rendered as integers;
    other floats use Python's shortest round-trip representation.  This keeps
    0.3 distinct from 0.30000000000000004 while unifying 0 and 0.0.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cache identity v2 rejects NaN and infinity")
        if value == 0.0 or value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_json_v2(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("cache identity v2 mapping keys must be strings")
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{_canonical_json_v2(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise TypeError(f"cache identity v2 does not support {type(value).__name__}")


def canonical_json_v2_bytes(value: Any) -> bytes:
    """Serialize supported values deterministically as UTF-8 schema-v2 JSON."""
    return _canonical_json_v2(value).encode("utf-8")


def cache_identity_v2(values: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Build a schema-v2 semantic identity including its schema version."""
    identity = {CACHE_SCHEMA_FIELD: CACHE_IDENTITY_SCHEMA_V2}
    identity.update({field: values.get(field) for field in CACHE_FIELDS})
    payload = canonical_json_v2_bytes(identity)
    return hashlib.sha256(payload).hexdigest(), identity


def cache_identity_v2_for_fields(
    values: Dict[str, Any], fields: Iterable[str]
) -> tuple[str, Dict[str, Any]]:
    """Build a schema-v2 key for an explicit, versioned identity contract.

    The Phase 9 fixed-field function remains unchanged so its approved A1
    admission keys stay reproducible.  New experiment contracts may enumerate
    additional identity fields, but planning and runtime must pass the same
    ordered field set and include an explicit contract identifier.
    """
    ordered_fields = tuple(fields)
    if len(ordered_fields) != len(set(ordered_fields)):
        raise ValueError("cache identity fields must be unique")
    if CACHE_SCHEMA_FIELD in ordered_fields:
        raise ValueError("schema version is injected and must not be duplicated")
    identity = {CACHE_SCHEMA_FIELD: CACHE_IDENTITY_SCHEMA_V2}
    identity.update({field: values.get(field) for field in ordered_fields})
    payload = canonical_json_v2_bytes(identity)
    return hashlib.sha256(payload).hexdigest(), identity


def cache_identity_for_schema(
    values: Dict[str, Any], schema_version: int
) -> tuple[str, Dict[str, Any]]:
    """Dispatch explicitly without treating v1 evidence as a v2 cache hit."""
    if schema_version == CACHE_IDENTITY_SCHEMA_V1:
        return cache_identity_v1(values)
    if schema_version == CACHE_IDENTITY_SCHEMA_V2:
        return cache_identity_v2(values)
    raise ValueError(f"unsupported cache identity schema version: {schema_version}")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, value: str) -> None:
    _atomic_write(path, value.encode("utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _safe(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "unknown")).strip("._")
    return (cleaned or "unknown")[:120]


def result_directory(
    output_root: Path,
    *,
    method: str,
    model: str,
    dataset_id: str,
    prompt_variant: str,
    seed: Optional[int],
) -> Path:
    return (
        output_root
        / _safe(method)
        / _safe(model)
        / _safe(dataset_id)
        / _safe(prompt_variant)
        / f"seed_{_safe(seed)}"
    )


def _parse(value: str) -> tuple[bool, Optional[str], Optional[str]]:
    if not value.strip():
        return False, None, "Empty content"
    leading = value.lstrip().lower()
    candidates = []
    if leading.startswith(("<?xml", "<rdf:rdf")):
        candidates.append("xml")
    elif leading.startswith(("{", "[")):
        candidates.append("json-ld")
    candidates.extend(["turtle", "xml", "json-ld", "nt"])
    errors = []
    for rdf_format in dict.fromkeys(candidates):
        try:
            Graph().parse(data=value, format=rdf_format)
            return True, rdf_format, None
        except Exception as exc:
            errors.append(f"{rdf_format}: {exc}")
    return False, None, " | ".join(errors)


def build_parse_metadata(
    raw_output: str,
    normalized_output: str,
    final_output: str,
    *,
    llm_repair_used: bool = False,
    repair_attempt_count: int = 0,
    repair_prompt_path: Optional[str] = None,
    repair_response_path: Optional[str] = None,
    prefix_repair_used: bool = False,
) -> Dict[str, Any]:
    raw_ok, raw_format, raw_error = _parse(raw_output)
    normalized_ok, normalized_format, normalized_error = _parse(normalized_output)
    final_ok, final_format, final_error = _parse(final_output)
    return {
        "raw_parse_success": raw_ok,
        "raw_parse_format": raw_format,
        "raw_parse_error": raw_error,
        "markdown_fence_removed": "```" in raw_output and "```" not in normalized_output,
        "prefix_repair_used": prefix_repair_used,
        "normalization_used": raw_output != normalized_output,
        "normalized_parse_success": normalized_ok,
        "normalized_parse_format": normalized_format,
        "normalized_parse_error": normalized_error,
        "llm_repair_used": llm_repair_used,
        "repair_attempt_count": repair_attempt_count,
        "repair_prompt_path": repair_prompt_path,
        "repair_response_path": repair_response_path,
        "final_parse_success": final_ok,
        "final_parse_format": final_format,
        "final_parse_error": final_error,
    }


def write_result_envelope(
    output_root: Path,
    *,
    dataset_id: str,
    method: str,
    model: str,
    prompt_variant: str,
    seed: Optional[int],
    final_ontology: str,
    adapter_metadata: Dict[str, Any],
    generation_metadata: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
) -> Path:
    directory = result_directory(
        output_root,
        method=method,
        model=model,
        dataset_id=dataset_id,
        prompt_variant=prompt_variant,
        seed=seed,
    )
    if directory.exists():
        archive = directory.with_name(
            f"{directory.name}__archive_{time.time_ns()}"
        )
        os.replace(directory, archive)
    calls = list(adapter_metadata.get("internal_calls") or [])
    prompts = [str(call.get("assembled_prompt") or "") for call in calls]
    raw_outputs = [str(call.get("raw_output") or "") for call in calls]
    assembled = "\n\n".join(
        f"# step_{index:02d}\n{prompt}" for index, prompt in enumerate(prompts, 1)
    )
    raw_output = "\n\n".join(raw_outputs)
    normalized = str(adapter_metadata.get("normalized_output") or final_ontology)
    raw_response = [call.get("raw_response") for call in calls]

    parse = build_parse_metadata(
        raw_output,
        normalized,
        final_ontology,
        llm_repair_used=bool(adapter_metadata.get("repair_attempt_count", 0)),
        repair_attempt_count=int(adapter_metadata.get("repair_attempt_count", 0)),
        repair_prompt_path=adapter_metadata.get("repair_prompt_path"),
        repair_response_path=adapter_metadata.get("repair_response_path"),
        prefix_repair_used=bool(adapter_metadata.get("prefix_repair_used", False)),
    )
    metadata = dict(generation_metadata or {})
    metadata.update(
        {
            "output_contract_version": OUTPUT_CONTRACT_VERSION,
            "dataset_id": dataset_id,
            "method": method,
            "method_system_id": method,
            "provider": adapter_metadata.get("provider"),
            "model": model,
            "prompt_variant": prompt_variant,
            "assembled_prompt_hash": sha256_text(assembled),
            "temperature": adapter_metadata.get("temperature"),
            "seed": seed,
            "num_ctx": adapter_metadata.get("num_ctx"),
            "max_output_tokens": adapter_metadata.get("max_output_tokens"),
            "timeout_seconds": adapter_metadata.get("timeout_seconds"),
            "keep_alive": adapter_metadata.get("keep_alive"),
            "retry_policy": adapter_metadata.get("retry_policy"),
            "internal_call_count": len(calls),
            "attempt_count": sum(int(call.get("attempts", 1)) for call in calls),
            "provider_telemetry": [call.get("telemetry") or {} for call in calls],
            "adapter_metadata": adapter_metadata,
        }
    )
    required_metadata_defaults = {
        "run_id": None,
        "original_story_id": None,
        "original_cq_ids": None,
        "method_family": method,
        "model_digest": None,
        "prompt_hash": None,
        "source_prompt_path": None,
        "prompt_snapshot_path": None,
        "procedure_hash": None,
        "odp_manifest_hash": None,
        "ODP_manifest_hash": metadata.get("odp_manifest_hash"),
        "instruction_boundary_id": None,
        "editable_instruction_characters": None,
        "normalized_edit_distance": None,
        "repair_policy": None,
        "start_time": None,
        "end_time": None,
        "wall_clock_seconds": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "repository_commit": None,
        "PR_baseline_commit": None,
        "official_main_commit": None,
        "resource_repository_commits": None,
        "Ollama_version": None,
        "dataset_manifest_hash": None,
        "experiment_config_hash": None,
    }
    for key, value in required_metadata_defaults.items():
        metadata.setdefault(key, value)
    schema_version = int(
        metadata.get(CACHE_SCHEMA_FIELD, CACHE_IDENTITY_SCHEMA_V2)
    )
    identity_fields = metadata.get("cache_identity_fields")
    if schema_version == CACHE_IDENTITY_SCHEMA_V2 and identity_fields is not None:
        if not isinstance(identity_fields, list) or not all(
            isinstance(field, str) for field in identity_fields
        ):
            raise TypeError("cache_identity_fields must be a list of strings")
        cache_key, cache_basis = cache_identity_v2_for_fields(
            metadata, identity_fields
        )
    else:
        cache_key, cache_basis = cache_identity_for_schema(metadata, schema_version)
    metadata[CACHE_SCHEMA_FIELD] = schema_version
    metadata["cache_key"] = cache_key
    metadata["cache_identity"] = cache_basis

    for index, call in enumerate(calls, start=1):
        step_dir = directory / "steps" / f"step_{index:02d}"
        step_normalized = str(call.get("normalized_output") or raw_outputs[index - 1])
        step_final = str(call.get("final_ontology") or step_normalized)
        atomic_write_text(step_dir / "assembled_prompt.txt", prompts[index - 1])
        atomic_write_json(step_dir / "request.json", call.get("request") or {})
        atomic_write_json(step_dir / "raw_response.json", call.get("raw_response"))
        atomic_write_text(step_dir / "raw_output.txt", raw_outputs[index - 1])
        atomic_write_text(step_dir / "normalized_output.txt", step_normalized)
        atomic_write_text(step_dir / "final_ontology.ttl", step_final)
        atomic_write_json(
            step_dir / "parse_metadata.json",
            build_parse_metadata(raw_outputs[index - 1], step_normalized, step_final),
        )
        if "previous_output_input" in call:
            atomic_write_text(
                step_dir / "previous_output_input.txt",
                str(call.get("previous_output_input") or ""),
            )
        step_metadata = {
            key: value
            for key, value in call.items()
            if key
            not in {
                "assembled_prompt",
                "request",
                "raw_response",
                "raw_output",
                "normalized_output",
                "final_ontology",
                "previous_output_input",
            }
        }
        step_metadata["assembled_prompt_sha256"] = sha256_text(prompts[index - 1])
        atomic_write_json(step_dir / "step_metadata.json", step_metadata)

    atomic_write_text(directory / "assembled_prompt.txt", assembled)
    atomic_write_json(directory / "raw_response.json", raw_response)
    atomic_write_text(directory / "raw_output.txt", raw_output)
    atomic_write_text(directory / "normalized_output.txt", normalized)
    atomic_write_text(directory / "final_ontology.ttl", final_ontology)
    atomic_write_json(directory / "generation_metadata.json", metadata)
    atomic_write_json(directory / "parse_metadata.json", parse)
    atomic_write_json(directory / "error.json", error or {"error": None})
    atomic_write_json(
        directory / "status.json",
        {
            "status": "failed" if error or not parse["final_parse_success"] else "success",
            "cache_key": cache_key,
            CACHE_SCHEMA_FIELD: schema_version,
            "required_files": list(REQUIRED_RESULT_FILES[:-1]),
        },
    )
    return directory


def result_state(
    directory: Path,
    expected_cache_key: Optional[str] = None,
    expected_schema_version: Optional[int] = None,
) -> str:
    if not directory.exists():
        return "missing"
    if any(not (directory / name).is_file() for name in REQUIRED_RESULT_FILES):
        return "incomplete"
    try:
        status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
        error = json.loads((directory / "error.json").read_text(encoding="utf-8"))
        parse = json.loads((directory / "parse_metadata.json").read_text(encoding="utf-8"))
        generation = json.loads(
            (directory / "generation_metadata.json").read_text(encoding="utf-8")
        )
    except Exception:
        return "incomplete"
    if generation.get("output_contract_version") == OUTPUT_CONTRACT_VERSION:
        call_count = generation.get("internal_call_count")
        calls = (generation.get("adapter_metadata") or {}).get("internal_calls") or []
        if not isinstance(call_count, int) or call_count < 0 or len(calls) != call_count:
            return "incomplete"
        for index, call in enumerate(calls, start=1):
            step_dir = directory / "steps" / f"step_{index:02d}"
            if any(not (step_dir / name).is_file() for name in REQUIRED_STEP_FILES):
                return "incomplete"
            if "previous_output_input" in call and not (
                step_dir / "previous_output_input.txt"
            ).is_file():
                return "incomplete"
    if expected_schema_version is not None:
        status_schema = status.get(CACHE_SCHEMA_FIELD)
        generation_schema = generation.get(CACHE_SCHEMA_FIELD)
        if (
            status_schema != expected_schema_version
            or generation_schema != expected_schema_version
        ):
            return "schema_mismatch"
    if expected_cache_key and status.get("cache_key") != expected_cache_key:
        return "stale"
    if status.get("status") == "success" and not error.get("error") and parse.get(
        "final_parse_success"
    ):
        return "success"
    return "failed"


def should_execute(state: str, *, resume: bool, retry_failed: bool) -> bool:
    if retry_failed:
        return state == "failed"
    if resume and state == "success":
        return False
    return True
