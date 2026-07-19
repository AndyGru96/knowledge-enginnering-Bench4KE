"""Execute the frozen Stage A1 plan after a separate owner authorization."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
RESTAPI = ROOT / "restapi"
for entry in (ROOT, RESTAPI):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app.models_ontology import OntologyBenchmarkRequest  # noqa: E402
from app.routers import ontology_benchmark as benchmark  # noqa: E402
from app.utils.ontology_artifacts import result_state  # noqa: E402
from app.utils.ontology_dataset import load_ontology_items  # noqa: E402
from scripts.prepare_stage_a1_preflight import (  # noqa: E402
    CONFIG_PATH,
    DATASET_PATH,
    FROZEN_HASHES,
    MODEL,
    MODEL_DIGEST,
    PLAN_CSV,
    PROMPT_HASHES,
    canonical_hash,
    sha,
    verify_runtime,
)
from scripts.run_phase6_local_pilot import _in_process_adapter  # noqa: E402


def load_frozen_config(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    expected = config.pop("experiment_config_hash")
    if canonical_hash(config) != expected:
        raise RuntimeError("Frozen Stage A1 configuration hash mismatch")
    config["experiment_config_hash"] = expected
    if config.get("status") != "frozen" or config["authorization"].get("stage_a1_generation_authorized"):
        raise RuntimeError("Configuration status/Phase 8 authorization boundary changed")
    if config["generation"]["num_ctx"] != 32768 or config["generation"]["num_predict"] != 8192:
        raise RuntimeError("Stage A1 context/output freeze changed")
    return config


def build_items(config: dict[str, Any]) -> list[Any]:
    source = {item.dataset_id: item for item in load_ontology_items(str(DATASET_PATH))}
    plan = list(csv.DictReader(PLAN_CSV.open(encoding="utf-8", newline="")))
    if len(plan) != 51 or [int(row["execution_order"]) for row in plan] != list(range(1, 52)):
        raise RuntimeError("Stage A1 task plan is missing or reordered")
    items = []
    for row in plan:
        base = source[row["dataset_id"]]
        method = row["method"]
        metadata = dict(base.metadata or {})
        metadata.update(
            {
                "provider": "ollama",
                "model": MODEL,
                "model_digest": MODEL_DIGEST,
                "temperature": 0,
                "seed": 42,
                "num_ctx": 32768,
                "max_output_tokens": 8192,
                "timeout_seconds": 1800,
                "keep_alive": "30m",
                "stream": False,
                "prompt_variant": "P0",
                "prompt_hash": PROMPT_HASHES[method],
                "source_prompt_path": f"datasets/ontology_generation/prompts/{method}/P0_original.txt",
                "prompt_snapshot_path": f"tests/snapshots/prompts/{method}",
                "procedure_hash": FROZEN_HASHES["procedure_sha256"],
                "odp_manifest_hash": FROZEN_HASHES["odp_manifest_sha256"],
                "repair_policy": "adapter-approved",
                "experiment_config_hash": config["experiment_config_hash"],
                "Ollama_version": "0.32.0",
                "official_main_commit": config["provenance"]["official_main_commit"],
                "resource_repository_commits": config["provenance"]["authoritative_resource_commits"],
                "a1_task_id": row["task_id"],
                "a1_execution_order": int(row["execution_order"]),
                "B6_monitor": row["B6_monitor"].lower() == "true",
            }
        )
        copy_method = getattr(base, "model_copy", None) or base.copy
        items.append(copy_method(update={"system": method, "metadata": metadata}))
    return items


def resume_cache_status(state: str) -> str:
    return {
        "success": "skipped_success",
        "failed": "held_failed",
        "incomplete": "held_incomplete",
        "stale": "held_stale_cache_mismatch",
        "missing": "reported_missing",
    }.get(state, f"held_{state}")


def read_only_resume(config: dict[str, Any]) -> dict[str, Any]:
    plan = list(csv.DictReader(PLAN_CSV.open(encoding="utf-8", newline="")))
    rows = []
    for task in plan:
        path = ROOT / task["output_directory"]
        state = result_state(path, task["cache_key"])
        rows.append(
            {
                "task_id": task["task_id"],
                "dataset_id": task["dataset_id"],
                "method": task["method"],
                "result_dir": str(path),
                "state": state,
                "cache_status": resume_cache_status(state),
                "new_internal_call_count": 0,
                "new_http_attempt_count": 0,
            }
        )
    return {
        "mode": "resume",
        "resume_policy": "read-only: matching success skipped; all other states held/reported",
        "config_hash": config["experiment_config_hash"],
        "actual_top_level_executions": 0,
        "actual_internal_calls": 0,
        "actual_http_attempts": 0,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--resume", action="store_true")
    modes.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()

    config = load_frozen_config(args.config)
    if sha(DATASET_PATH) != FROZEN_HASHES["dataset_full_generation_sha256"]:
        raise RuntimeError("Frozen Stage A1 dataset hash changed")
    items = build_items(config)
    if args.dry_run:
        print(json.dumps({"mode": "dry-run", "generation_calls": 0, "tasks": len(items), "config_hash": config["experiment_config_hash"]}, indent=2))
        return 0

    verify_runtime()
    if args.resume:
        report = read_only_resume(config)
        path = ROOT / "reports/stage_a1_resume_execution.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "mode": "resume",
            "tasks": len(report["results"]),
            "actual_top_level_executions": 0,
            "actual_internal_calls": 0,
            "actual_http_attempts": 0,
            "cache_status_counts": {
                status: sum(row["cache_status"] == status for row in report["results"])
                for status in sorted({row["cache_status"] for row in report["results"]})
            },
            "report": str(path),
        }, indent=2))
        return 0

    os.environ.update(
        {
            "LLM_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "OOPS_API_URL": "",
            "HERMIT_MODE": "",
            "HERMIT_AUTO": "false",
        }
    )
    benchmark.call_external_ontology_service = _in_process_adapter
    benchmark.ONTOLOGY_RUNS_DIR = ROOT / "outputs/stage_a1"
    request = OntologyBenchmarkRequest(
        system="all",
        items=items,
        provider="ollama",
        model=MODEL,
        temperature=0,
        seed=42,
        num_ctx=32768,
        max_output_tokens=8192,
        timeout_seconds=1800,
        keep_alive="30m",
        prompt_variant="P0",
        resume=args.resume,
        retry_failed=args.retry_failed,
        evaluation_mode="none",
        domain_ontogen_mode="per_item",
        save_results=True,
    )
    response = benchmark.run_ontology_benchmark(request)
    mode = "resume" if args.resume else "retry-failed" if args.retry_failed else "execute"
    report = {
        "mode": mode,
        "config_hash": config["experiment_config_hash"],
        "run_dir": response.run_dir,
        "summary_file": response.results_saved_to,
        "results": [
            {
                "dataset_id": row.dataset_id,
                "method": row.system,
                "cache_status": row.cache_status,
                "result_dir": row.result_dir,
                "error": row.error,
            }
            for row in response.results
        ],
    }
    path = ROOT / "reports" / f"stage_a1_{mode.replace('-', '_')}_execution.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": mode, "tasks": len(response.results), "report": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
