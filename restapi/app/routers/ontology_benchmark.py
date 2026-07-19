import json
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.config import (
    EXTERNAL_ONTOLOGY_SERVICE_URL,
    ONTOLOGY_DATASET_DIR,
    ONTOLOGY_EXTERNAL_TIMEOUT,
    ONTOLOGY_RUNS_DIR,
    ONTOLOGY_PROJECT2_OUTPUT_DIR,
    OOPS_API_MODE,
    OOPS_API_TIMEOUT,
    OOPS_API_URL,
    ONTOLOGY_LLM_EVAL_MAX_CHARS,
    ONTOLOGY_LLM_EVAL_MAX_TOKENS,
    ONTOLOGY_LLM_EVAL_MODEL,
    ONTOLOGY_LLM_EVAL_PROMPT_PATH,
)
from app.models_ontology import (
    OntologyBenchmarkRequest,
    OntologyBenchmarkResponse,
    OntologyRunItemResult,
)
from app.services.ontology_llm_eval import evaluate_ontology_with_llm
from app.services.ontology_metrics import compute_ontometrics
from app.services.ontology_oops import run_oops_scan
from app.utils.ontology_dataset import load_ontology_items
from app.utils.ontology_external_call import call_external_ontology_service
from app.utils.ontology_artifacts import (
    A2_CACHE_FIELDS,
    A2_CACHE_IDENTITY_CONTRACT,
    CACHE_IDENTITY_SCHEMA_V2,
    CACHE_SCHEMA_FIELD,
    cache_identity_v2,
    cache_identity_v2_for_fields,
    result_directory,
    result_state,
    should_execute,
    write_result_envelope,
)


router = APIRouter()
_CONFIGURED_RUNS_DIR = ONTOLOGY_RUNS_DIR
_FROZEN_PROMPT_HASHES = {
    "ontogenia": "f91ec50dd4d6e6a0219df892212c7beecbe74db4ef54a075d3b177d9194f7965",
    "domain-ontogen": "f9e3945421508cd6a82613caf0d26fe802084178d950b2f1bd81b0446c2add4e",
    "neon-gpt": "40d0baf11f4945fc37f0a4d2f67a7efbbf3a249e0ae8e5b105672ee79a83f44a",
}
_DATASET_MANIFEST_HASH = "e06831a155503aa5c2faa8312b7bd78eb6778b124f31dbfb1617bc63c6664caf"
_PR_BASELINE_COMMIT = "1488aed14b41305495d27435174d635e2ba2ebb4"


def _safe_filename(value: Optional[str]) -> str:
    if not value:
        return "item"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    if not cleaned:
        return "item"
    return cleaned[:80]


def _parse_evaluation_mode(mode: str) -> set[str]:
    if not mode:
        return {"ontometrics", "oops", "llm"}
    normalized = mode.strip().lower()
    if normalized == "all":
        return {"ontometrics", "oops", "llm"}
    parts = re.split(r"[,\s+|]+", normalized)
    return {part for part in parts if part}


@router.post("/run", response_model=OntologyBenchmarkResponse)
def run_ontology_benchmark(
    req: OntologyBenchmarkRequest,
) -> OntologyBenchmarkResponse:
    items = req.items or []
    dataset_path = None
    if not items:
        if not req.use_default_dataset and not req.dataset_path:
            raise HTTPException(
                status_code=400,
                detail="Provide items or set use_default_dataset=True or dataset_path.",
            )
        dataset_path = req.dataset_path or ONTOLOGY_DATASET_DIR
        try:
            items = load_ontology_items(dataset_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    system_filter = (req.system or "").strip().lower()
    if system_filter and system_filter != "all":
        filtered = []
        for item in items:
            item_system = (item.system or "").strip().lower()
            if not item_system:
                item_system = system_filter
            if item_system == system_filter:
                filtered.append(item)
        items = filtered

    if req.max_items > 0:
        items = items[: req.max_items]

    if not items:
        raise HTTPException(status_code=400, detail="No dataset items to process.")

    # Domain-OntoGen paper uses an "Independent Ontology Generation" protocol (one ontology per CQ).
    # Our default benchmark item format is one story/scenario with multiple CQs. When requested, we
    # expand Domain-OntoGen items into per-CQ items (paper-style) while keeping the external API
    # contract unchanged.
    domain_ontogen_mode = (req.domain_ontogen_mode or "per_item").strip().lower()
    if domain_ontogen_mode not in {"per_item", "per_cq"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid domain_ontogen_mode. Use 'per_item' or 'per_cq'.",
        )

    if domain_ontogen_mode == "per_cq":
        expanded = []
        for item in items:
            if (item.system or "").strip().lower() != "domain-ontogen":
                expanded.append(item)
                continue
            if len(item.competency_questions) <= 1:
                expanded.append(item)
                continue
            base_id = item.dataset_id or item.scenario_id or "item"
            for cq_idx, cq in enumerate(item.competency_questions, start=1):
                # Keep the story, but generate an ontology per CQ (independent).
                md = dict(item.metadata or {})
                md.setdefault("domain_ontogen_cq_index", cq_idx)
                md.setdefault("domain_ontogen_cq_text", cq)
                copy_method = getattr(item, "model_copy", None) or item.copy
                expanded.append(
                    copy_method(
                        update={
                            "dataset_id": f"{base_id}__cq{cq_idx}",
                            "competency_questions": [cq],
                            "metadata": md,
                        }
                    )
                )
        items = expanded

    external_url = req.external_service_url or EXTERNAL_ONTOLOGY_SERVICE_URL

    run_dir = None
    results_file = None
    if req.save_results:
        run_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        run_dir = Path(ONTOLOGY_RUNS_DIR) / f"run_{run_id}"
        (run_dir / "ontologies").mkdir(parents=True, exist_ok=True)

    evaluation_set = _parse_evaluation_mode(req.evaluation_mode)
    metrics_dir = None
    sparql_dir = None
    if req.save_results and run_dir:
        metrics_dir = run_dir / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        sparql_dir = metrics_dir / "sparql_queries"
        sparql_dir.mkdir(parents=True, exist_ok=True)

    llm_aggregate_yes = 0
    llm_aggregate_total = 0
    # Aggregation required by the project spec: percentage of yes/no per dataset/system/model.
    # We track both generator model (ontology generation) and eval model (OE-Assist).
    llm_aggregate_by_group: dict[
        tuple[str, str, str, str], dict[str, int]
    ] = defaultdict(lambda: {"yes": 0, "no": 0, "total": 0, "items": 0})

    results = []
    for idx, item in enumerate(items, start=1):
        dump_method = getattr(item, "model_dump", None) or item.dict
        payload = dump_method(exclude_none=True)
        if system_filter and system_filter != "all" and not payload.get("system"):
            payload["system"] = system_filter
        if req.model:
            metadata = dict(payload.get("metadata") or {})
            metadata.setdefault("model", req.model)
            payload["metadata"] = metadata

        request_metadata = dict(payload.get("metadata") or {})
        request_metadata.setdefault("provider", req.provider)
        request_metadata.setdefault("temperature", req.temperature)
        request_metadata.setdefault("seed", req.seed)
        request_metadata.setdefault("num_ctx", req.num_ctx)
        request_metadata.setdefault("max_output_tokens", req.max_output_tokens)
        request_metadata.setdefault("timeout_seconds", req.timeout_seconds)
        request_metadata.setdefault("keep_alive", req.keep_alive)
        request_metadata.setdefault(CACHE_SCHEMA_FIELD, CACHE_IDENTITY_SCHEMA_V2)
        payload["metadata"] = request_metadata

        item_system = payload.get("system")
        item_id = payload.get("dataset_id") or payload.get("scenario_id") or f"item_{idx}"

        model_name = str(request_metadata.get("model") or req.model or "unknown")
        is_a2_identity = (
            request_metadata.get("cache_identity_contract")
            == A2_CACHE_IDENTITY_CONTRACT
        )
        runtime_prompt_hash = (
            request_metadata.get("prompt_hash")
            if is_a2_identity
            else _FROZEN_PROMPT_HASHES.get(str(item_system))
        )
        cache_values = {
            CACHE_SCHEMA_FIELD: request_metadata.get(CACHE_SCHEMA_FIELD),
            "dataset_id": str(item_id),
            "method": item_system,
            "provider": request_metadata.get("provider"),
            "model": model_name,
            "model_digest": request_metadata.get("model_digest"),
            "prompt_hash": runtime_prompt_hash,
            "temperature": request_metadata.get("temperature"),
            "seed": request_metadata.get("seed"),
            "num_ctx": request_metadata.get("num_ctx"),
            "max_output_tokens": request_metadata.get("max_output_tokens"),
            "procedure_hash": request_metadata.get("procedure_hash"),
            "odp_manifest_hash": request_metadata.get("odp_manifest_hash"),
            "repair_policy": request_metadata.get("repair_policy", "adapter-approved"),
            "repository_commit": _PR_BASELINE_COMMIT,
            "dataset_manifest_hash": _DATASET_MANIFEST_HASH,
            "experiment_config_hash": request_metadata.get("experiment_config_hash"),
        }
        if cache_values[CACHE_SCHEMA_FIELD] != CACHE_IDENTITY_SCHEMA_V2:
            raise HTTPException(
                status_code=400,
                detail="Future benchmark runs require cache_identity_schema_version=2.",
            )
        if is_a2_identity:
            cache_values.update(
                {
                    "cache_identity_contract": A2_CACHE_IDENTITY_CONTRACT,
                    "dataset_record_hash": request_metadata.get("dataset_record_hash"),
                    "prompt_variant": req.prompt_variant,
                    "prompt_hash": runtime_prompt_hash,
                    "normalization_policy": request_metadata.get(
                        "normalization_policy"
                    ),
                    "parser_output_contract_version": request_metadata.get(
                        "parser_output_contract_version"
                    ),
                    "code_identity_hash": request_metadata.get("code_identity_hash"),
                    "cache_identity_fields": list(A2_CACHE_FIELDS),
                }
            )
            missing = [
                field
                for field in A2_CACHE_FIELDS
                if cache_values.get(field) is None
            ]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"A2 cache identity is missing required fields: {missing}",
                )
            expected_cache_key, _cache_basis = cache_identity_v2_for_fields(
                cache_values, A2_CACHE_FIELDS
            )
        else:
            expected_cache_key, _cache_basis = cache_identity_v2(cache_values)
        project2_root = Path(ONTOLOGY_PROJECT2_OUTPUT_DIR)
        if ONTOLOGY_RUNS_DIR != _CONFIGURED_RUNS_DIR:
            project2_root = Path(ONTOLOGY_RUNS_DIR) / "project2"
        expected_result_dir = result_directory(
            project2_root,
            method=str(item_system or "unknown"),
            model=model_name,
            dataset_id=str(item_id),
            prompt_variant=req.prompt_variant,
            seed=req.seed,
        )
        cached_state = result_state(
            expected_result_dir,
            expected_cache_key,
            expected_schema_version=CACHE_IDENTITY_SCHEMA_V2,
        )
        execute = should_execute(
            cached_state, resume=req.resume, retry_failed=req.retry_failed
        )
        if req.retry_failed and cached_state != "failed":
            execute = False
        if not execute:
            results.append(
                OntologyRunItemResult(
                    dataset_id=str(item_id),
                    system=item_system,
                    ontology_file=(
                        str(expected_result_dir / "final_ontology.ttl")
                        if cached_state == "success"
                        else None
                    ),
                    result_dir=str(expected_result_dir),
                    generation_metadata_file=str(
                        expected_result_dir / "generation_metadata.json"
                    ),
                    parse_metadata_file=str(expected_result_dir / "parse_metadata.json"),
                    cache_status=f"skipped_{cached_state}",
                )
            )
            continue

        generation_started = time.time()
        generation_started_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(generation_started)
        )
        try:
            response = call_external_ontology_service(
                payload, external_url, ONTOLOGY_EXTERNAL_TIMEOUT
            )
            generation_finished = time.time()
            generation_finished_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(generation_finished)
            )
            ontology = response.get("ontology") or {}
            content = ontology.get("content") or ""
            ontology_format = (ontology.get("format") or "ttl").lstrip(".")
            response_metadata = response.get("metadata") or {}
            internal_calls = list(response_metadata.get("internal_calls") or [])
            prompt_characters = sum(
                len(str(call.get("assembled_prompt") or ""))
                for call in internal_calls
            )
            raw_output_characters = sum(
                len(str(call.get("raw_output") or "")) for call in internal_calls
            )
            prompt_tokens = sum(
                int((call.get("telemetry") or {}).get("prompt_eval_count") or 0)
                for call in internal_calls
            )
            completion_tokens = sum(
                int((call.get("telemetry") or {}).get("eval_count") or 0)
                for call in internal_calls
            )

            ontology_file = None
            result_envelope_dir = None
            generation_metadata_file = None
            parse_metadata_file = None
            if req.save_results and run_dir:
                safe_id = _safe_filename(str(item_id))
                filename = f"{safe_id}_{idx}.{ontology_format or 'ttl'}"
                ontology_path = run_dir / "ontologies" / filename
                ontology_path.write_text(content, encoding="utf-8")
                ontology_file = str(ontology_path)
                result_envelope_dir = write_result_envelope(
                    project2_root,
                    dataset_id=str(item_id),
                    method=str(item_system or "unknown"),
                    model=str(response_metadata.get("model") or model_name),
                    prompt_variant=req.prompt_variant,
                    seed=req.seed,
                    final_ontology=content,
                    adapter_metadata=response_metadata,
                    generation_metadata={
                        **cache_values,
                        "source_prompt_path": request_metadata.get("source_prompt_path"),
                        "prompt_snapshot_path": request_metadata.get(
                            "prompt_snapshot_path"
                        ),
                        "official_main_commit": request_metadata.get(
                            "official_main_commit"
                        ),
                        "resource_repository_commits": request_metadata.get(
                            "resource_repository_commits"
                        ),
                        "Ollama_version": request_metadata.get("Ollama_version")
                        or request_metadata.get("ollama_version"),
                        "start_time": generation_started_at,
                        "end_time": generation_finished_at,
                        "wall_clock_seconds": generation_finished
                        - generation_started,
                        "prompt_characters": prompt_characters,
                        "raw_output_characters": raw_output_characters,
                        "final_output_characters": len(content),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "run_id": run_dir.name,
                        "original_story_id": (item.metadata or {}).get("original_story_id"),
                        "original_cq_ids": (item.metadata or {}).get("original_cq_ids"),
                        "method_family": str(item_system or "unknown"),
                        "repository_commit": _PR_BASELINE_COMMIT,
                        "PR_baseline_commit": _PR_BASELINE_COMMIT,
                        "dataset_manifest_hash": _DATASET_MANIFEST_HASH,
                    },
                )
                ontology_file = str(result_envelope_dir / "final_ontology.ttl")
                generation_metadata_file = str(
                    result_envelope_dir / "generation_metadata.json"
                )
                parse_metadata_file = str(result_envelope_dir / "parse_metadata.json")

            ontometrics_file = None
            oops_file = None
            llm_eval_file = None
            llm_eval_summary = None

            if content:
                if "ontometrics" in evaluation_set:
                    ontometrics = None
                    try:
                        ontometrics = compute_ontometrics(content, ontology_format)
                    except Exception as exc:
                        ontometrics = {"error": str(exc)}
                    if metrics_dir:
                        ontometrics_file = str(
                            metrics_dir / f"{_safe_filename(str(item_id))}_{idx}_ontometrics.json"
                        )
                        Path(ontometrics_file).write_text(
                            json.dumps(ontometrics, indent=2), encoding="utf-8"
                        )

                if "oops" in evaluation_set:
                    oops_result = None
                    try:
                        oops_result = run_oops_scan(
                            content, OOPS_API_URL, OOPS_API_TIMEOUT, OOPS_API_MODE
                        )
                    except Exception as exc:
                        oops_result = {"error": str(exc)}
                    if metrics_dir:
                        oops_file = str(
                            metrics_dir / f"{_safe_filename(str(item_id))}_{idx}_oops.json"
                        )
                        Path(oops_file).write_text(
                            json.dumps(oops_result, indent=2), encoding="utf-8"
                        )

                if "llm" in evaluation_set:
                    llm_model = req.llm_eval_model or ONTOLOGY_LLM_EVAL_MODEL
                    story = item.scenario or ""
                    if not story and item.user_stories:
                        story = "\n".join(item.user_stories)
                    llm_results, llm_eval_summary = evaluate_ontology_with_llm(
                        ontology_text=content,
                        competency_questions=item.competency_questions,
                        story=story,
                        prompt_path=ONTOLOGY_LLM_EVAL_PROMPT_PATH,
                        model=llm_model,
                        max_tokens=ONTOLOGY_LLM_EVAL_MAX_TOKENS,
                        max_chars=ONTOLOGY_LLM_EVAL_MAX_CHARS,
                    )
                    if metrics_dir:
                        llm_eval_file = str(
                            metrics_dir / f"{_safe_filename(str(item_id))}_{idx}_llm_eval.json"
                        )
                        Path(llm_eval_file).write_text(
                            json.dumps(
                                {"summary": llm_eval_summary, "results": llm_results},
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                    if sparql_dir:
                        sparql_path = sparql_dir / f"{_safe_filename(str(item_id))}_{idx}.sparql"
                        sparql_content = "\n\n".join(
                            [r.get("sparql", "") for r in llm_results if r.get("sparql")]
                        )
                        sparql_path.write_text(sparql_content, encoding="utf-8")
                    if llm_eval_summary:
                        llm_aggregate_yes += llm_eval_summary.get("yes", 0)
                        llm_aggregate_total += llm_eval_summary.get("total", 0)
                        # Group aggregation: dataset_name/system/generator_model/eval_model.
                        dataset_name = "inline"
                        if item.metadata and item.metadata.get("dataset_name"):
                            dataset_name = str(item.metadata.get("dataset_name"))
                        generator_model = (
                            str(response_metadata.get("model"))
                            if response_metadata.get("model")
                            else str((payload.get("metadata") or {}).get("model") or req.model or "unknown")
                        )
                        group_key = (
                            dataset_name,
                            str(item_system or "unknown"),
                            generator_model,
                            str(llm_model or "unknown"),
                        )
                        g = llm_aggregate_by_group[group_key]
                        g["yes"] += int(llm_eval_summary.get("yes", 0))
                        g["no"] += int(llm_eval_summary.get("no", 0))
                        g["total"] += int(llm_eval_summary.get("total", 0))
                        g["items"] += 1

            results.append(
                OntologyRunItemResult(
                    dataset_id=str(item_id),
                    system=item_system,
                    ontology_file=ontology_file,
                    ontometrics_file=ontometrics_file,
                    oops_file=oops_file,
                    llm_eval_file=llm_eval_file,
                    llm_eval_summary=llm_eval_summary,
                    result_dir=str(result_envelope_dir) if result_envelope_dir else None,
                    generation_metadata_file=generation_metadata_file,
                    parse_metadata_file=parse_metadata_file,
                    adapter_metadata=response_metadata,
                    cache_status="written",
                )
            )
        except Exception as exc:
            failed_dir = None
            failure_adapter_metadata = getattr(exc, "adapter_metadata", None) or {
                "provider": request_metadata.get("provider"),
                "model": model_name,
                "internal_calls": [],
                "temperature": request_metadata.get("temperature"),
                "seed": request_metadata.get("seed"),
                "num_ctx": request_metadata.get("num_ctx"),
                "max_output_tokens": request_metadata.get("max_output_tokens"),
                "timeout_seconds": request_metadata.get("timeout_seconds"),
                "keep_alive": request_metadata.get("keep_alive"),
            }
            if req.save_results:
                failed_dir = write_result_envelope(
                    project2_root,
                    dataset_id=str(item_id),
                    method=str(item_system or "unknown"),
                    model=model_name,
                    prompt_variant=req.prompt_variant,
                    seed=req.seed,
                    final_ontology="",
                    adapter_metadata=failure_adapter_metadata,
                    generation_metadata=cache_values,
                    error={"error": str(exc), "category": "generation_error"},
                )
            results.append(
                OntologyRunItemResult(
                    dataset_id=str(item_id),
                    system=item_system,
                    error=str(exc),
                    result_dir=str(failed_dir) if failed_dir else None,
                    generation_metadata_file=(
                        str(failed_dir / "generation_metadata.json") if failed_dir else None
                    ),
                    parse_metadata_file=(
                        str(failed_dir / "parse_metadata.json") if failed_dir else None
                    ),
                    adapter_metadata=failure_adapter_metadata,
                    cache_status="failed_preserved",
                )
            )

    if req.save_results and run_dir:
        run_metadata = {
            "run_id": run_dir.name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "system_filter": system_filter or "all",
            "dataset_path": dataset_path,
            "item_count": len(items),
            "external_service_url": external_url,
            "model_override": req.model,
            "evaluation_mode": req.evaluation_mode,
            "llm_eval_model": req.llm_eval_model or ONTOLOGY_LLM_EVAL_MODEL,
            "domain_ontogen_mode": domain_ontogen_mode,
            "oops_api_url": OOPS_API_URL or None,
        }
        (run_dir / "run_metadata.json").write_text(
            json.dumps(run_metadata, indent=2), encoding="utf-8"
        )

        llm_aggregate = None
        if llm_aggregate_total:
            llm_aggregate = {
                "yes": llm_aggregate_yes,
                "no": llm_aggregate_total - llm_aggregate_yes,
                "total": llm_aggregate_total,
                "yes_ratio": llm_aggregate_yes / llm_aggregate_total,
            }

        llm_aggregate_by_dataset_system_model = []
        for (dataset_name, system_name, generator_model, eval_model), counts in sorted(
            llm_aggregate_by_group.items()
        ):
            total = counts["total"]
            llm_aggregate_by_dataset_system_model.append(
                {
                    "dataset_name": dataset_name,
                    "system": system_name,
                    "generator_model": generator_model,
                    "eval_model": eval_model,
                    "yes": counts["yes"],
                    "no": counts["no"],
                    "total": total,
                    "yes_ratio": (counts["yes"] / total) if total else 0.0,
                    "items": counts["items"],
                }
            )

        summary = {
            "results": [
                (getattr(r, "model_dump", None) or r.dict)() for r in results
            ],
            "llm_eval_aggregate": llm_aggregate,
            "llm_eval_aggregate_by_dataset_system_model": llm_aggregate_by_dataset_system_model,
        }
        results_file = run_dir / "summary.json"
        results_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return OntologyBenchmarkResponse(
        message="Processing complete",
        run_dir=str(run_dir) if run_dir else None,
        results_saved_to=str(results_file) if results_file else "Not saved",
        results=results,
    )
