"""Execute the frozen Stage A2 plan only after separate owner authorization."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESTAPI = ROOT / "restapi"
for entry in (ROOT, RESTAPI):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app.models_ontology import OntologyBenchmarkRequest  # noqa: E402
from app.routers import ontology_benchmark as benchmark  # noqa: E402
from app.utils.ontology_artifacts import (  # noqa: E402
    A2_CACHE_FIELDS,
    CACHE_IDENTITY_SCHEMA_V2,
    result_state,
)
from app.utils.ontology_dataset import load_ontology_items  # noqa: E402
from scripts.phase11_a2_contract import (  # noqa: E402
    CONFIG_PATH,
    DATASET_PATH,
    MODEL,
    OUTPUT_ROOT,
    PLAN_CSV,
    action_for_state,
    cache_values_for_task,
    load_frozen_config,
    load_plan,
    validate_plan,
)
from scripts.prepare_stage_a1_preflight import verify_runtime  # noqa: E402
from scripts.run_phase6_local_pilot import _in_process_adapter  # noqa: E402


def build_item(config: dict[str, Any], task: dict[str, Any], source: dict[str, Any]) -> Any:
    base = source[task["dataset_id"]]
    metadata = dict(base.metadata or {})
    prompt = config["prompt_variants"][task["method"]][task["prompt_variant"]]
    identity = cache_values_for_task(config, task)
    metadata.update(
        {
            **identity,
            "cache_identity_schema_version": CACHE_IDENTITY_SCHEMA_V2,
            "cache_identity_fields": list(A2_CACHE_FIELDS),
            "prompt_template": prompt["source_file"],
            "source_prompt_path": prompt["source_file"],
            "timeout_seconds": 1800,
            "keep_alive": "30m",
            "stream": False,
            "a2_task_id": task["task_id"],
            "a2_execution_order": int(task["execution_order"]),
            "baseline_P0_task_id": task["baseline_P0_task_id"],
            "baseline_A1_admission_entry": task["baseline_A1_admission_entry"],
            "baseline_P0_schema_v2_key": task["baseline_P0_schema_v2_key"],
            "B6_monitor": task["method"] == "ontogenia",
            "length_monitor": True,
            "prompt_sensitivity_monitor": True,
        }
    )
    copy_method = getattr(base, "model_copy", None) or base.copy
    return copy_method(update={"system": task["method"], "metadata": metadata})


def classify_plan(config: dict[str, Any], plan: list[dict[str, Any]], mode: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    classified: list[dict[str, Any]] = []
    for task in plan:
        state = result_state(
            ROOT / task["output_directory"],
            task["schema_v2_cache_key"],
            expected_schema_version=CACHE_IDENTITY_SCHEMA_V2,
        )
        action = action_for_state(state, mode)
        row = {"task_id": task["task_id"], "state": state, "action": action}
        classified.append(row)
        if action == "execute":
            selected.append(task)
        elif action.startswith("block_"):
            raise RuntimeError(f"A2 {mode} blocked by {row}")
    return selected, classified


def execute_selected(config: dict[str, Any], selected: list[dict[str, Any]], *, retry_failed: bool) -> list[dict[str, Any]]:
    source = {item.dataset_id: item for item in load_ontology_items(str(DATASET_PATH))}
    os.environ.update(
        {
            "LLM_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": config["provider"]["base_url"],
            "OOPS_API_URL": "",
            "HERMIT_MODE": "",
            "HERMIT_AUTO": "false",
        }
    )
    benchmark.call_external_ontology_service = _in_process_adapter
    benchmark.ONTOLOGY_RUNS_DIR = ROOT / "outputs/stage_a2"
    results: list[dict[str, Any]] = []
    for task in selected:
        item = build_item(config, task, source)
        request = OntologyBenchmarkRequest(
            system=task["method"], items=[item], provider="ollama", model=MODEL,
            temperature=0, seed=42, num_ctx=32768, max_output_tokens=8192,
            timeout_seconds=1800, keep_alive="30m", prompt_variant=task["prompt_variant"],
            resume=False, retry_failed=retry_failed, evaluation_mode="none",
            domain_ontogen_mode="per_item", save_results=True,
        )
        response = benchmark.run_ontology_benchmark(request)
        if len(response.results) != 1:
            raise RuntimeError(f"A2 task returned unexpected result count: {task['task_id']}")
        row = response.results[0]
        if Path(str(row.result_dir)).resolve() != (ROOT / task["output_directory"]).resolve():
            raise RuntimeError(f"A2 result path mismatch: {task['task_id']}")
        results.append(
            {
                "task_id": task["task_id"], "dataset_id": task["dataset_id"],
                "method": task["method"], "prompt_variant": task["prompt_variant"],
                "cache_status": row.cache_status, "result_dir": row.result_dir,
                "error": row.error,
            }
        )
    return results


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
    plan = validate_plan(config, load_plan(PLAN_CSV))
    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": "dry-run", "tasks": len(plan),
                    "config_hash": config["experiment_config_hash"],
                    "generation_calls": 0, "provider_http_generation_attempts": 0,
                }, indent=2
            )
        )
        return 0

    mode = "execute" if args.execute else "resume" if args.resume else "retry-failed"
    selected, classified = classify_plan(config, plan, mode)
    runtime = verify_runtime() if selected else {"generation_endpoint_calls": 0, "not_needed": True}
    results = execute_selected(config, selected, retry_failed=args.retry_failed) if selected else []
    report = {
        "mode": mode, "config_hash": config["experiment_config_hash"],
        "planned_tasks": len(plan), "selected_tasks": len(selected),
        "pre_execution_actions": dict(Counter(row["action"] for row in classified)),
        "runtime_preflight": runtime, "results": results,
    }
    path = ROOT / "reports" / f"stage_a2_{mode.replace('-', '_')}_execution.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": mode, "selected_tasks": len(selected), "report": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
