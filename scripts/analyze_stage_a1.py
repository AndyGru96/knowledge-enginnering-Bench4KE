"""Audit the immutable Stage A1 generation evidence and export review tables."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from rdflib import Graph


ROOT = Path(__file__).resolve().parents[1]
RESTAPI = ROOT / "restapi"
for entry in (ROOT, RESTAPI):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app.services.ontology_metrics import compute_ontometrics  # noqa: E402
from app.utils.ontology_artifacts import REQUIRED_RESULT_FILES, REQUIRED_STEP_FILES  # noqa: E402


RESULTS = ROOT / "results"
PLAN = RESULTS / "a1_task_plan.csv"
EXECUTION_REPORT = ROOT / "reports/stage_a1_execute_execution.json"
DATASET = ROOT / "datasets/ontology_generation/normalized/project2_full_generation.jsonl"
EXPECTED = {
    "model": "qwen3:30b-a3b-instruct-2507-q4_K_M",
    "model_digest": "19e422b0231392335cfc49cfd172de7034bb1aeabb08aa307cce745c60b272fe",
    "seed": 42,
    "temperature": 0.0,
    "num_ctx": 32768,
    "max_output_tokens": 8192,
    "experiment_config_hash": "a3772fff9fadaf236dcb2b390b60a97ff4b220862bfc609fc2483013203e509e",
}


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_turtle(text: str) -> tuple[bool, str]:
    if not text.strip():
        return False, "Empty content"
    try:
        Graph().parse(data=text, format="turtle")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def error_category(error: str, content: str) -> str:
    value = f"{error}\n{content}".lower()
    if "prefix" in value and ("not bound" in value or "not defined" in value):
        return "unbound_prefix"
    disjoint_lines = [line for line in content.splitlines() if "disjointWith" in line]
    if any(re.search(r"disjointWith\s+:[^,;\.\s]+\s+:[^,;\.\s]+", line) for line in disjoint_lines):
        return "malformed_disjointwith_object_list"
    if "disjointwith" in value:
        return "malformed_disjointness_syntax"
    if "unterminated" in value or "eof" in value or "end of file" in value:
        return "truncated_or_unterminated_turtle"
    if "bad syntax" in value or "expected" in value:
        return "general_turtle_syntax"
    if "empty content" in value:
        return "empty_final_output"
    return "other_parse_failure"


def bool_count(rows: list[dict[str, Any]], key: str) -> int:
    return sum(row.get(key) is True for row in rows)


def main() -> int:
    plans = list(csv.DictReader(PLAN.open(encoding="utf-8", newline="")))
    execution = read_json(EXECUTION_REPORT)
    execution_rows = execution.get("results") or []
    execution_index = {(row.get("dataset_id"), row.get("method")): row for row in execution_rows}
    raw_records = {
        row["dataset_id"]: row
        for row in (json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip())
    }

    completeness: list[dict[str, Any]] = []
    parse_rows: list[dict[str, Any]] = []
    step_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    b6_rows: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    metric_failures: list[dict[str, Any]] = []

    for task in plans:
        directory = ROOT / task["output_directory"]
        missing_top: list[str] = []
        missing_steps: list[str] = []
        corrupt_files: list[str] = []
        stale_fields: list[str] = []
        telemetry_missing: list[str] = []
        classification = "missing"
        status = generation = parse = error = None
        actual_cache_self_consistent = False
        semantic_identity_values_match = False
        cache_mismatch_cause = ""
        actual_assembled_prompt_hash_self_consistent = False
        assembled_prompt_evidence_match = False

        if directory.exists():
            missing_top = [name for name in REQUIRED_RESULT_FILES if not (directory / name).is_file()]
            if missing_top:
                classification = "incomplete"
            else:
                for name in ("status.json", "generation_metadata.json", "parse_metadata.json", "error.json", "raw_response.json"):
                    try:
                        read_json(directory / name)
                    except Exception:
                        corrupt_files.append(name)
                if corrupt_files:
                    classification = "corrupt"
                else:
                    status = read_json(directory / "status.json")
                    generation = read_json(directory / "generation_metadata.json")
                    parse = read_json(directory / "parse_metadata.json")
                    error = read_json(directory / "error.json")
                    calls = list((generation.get("adapter_metadata") or {}).get("internal_calls") or [])
                    declared = generation.get("internal_call_count")
                    if not isinstance(declared, int) or declared != len(calls):
                        missing_steps.append("declared_call_count_mismatch")
                    for index, call in enumerate(calls, start=1):
                        step_dir = directory / "steps" / f"step_{index:02d}"
                        for name in REQUIRED_STEP_FILES:
                            if not (step_dir / name).is_file():
                                missing_steps.append(f"step_{index:02d}/{name}")
                        if "previous_output_input" in call and not (step_dir / "previous_output_input.txt").is_file():
                            missing_steps.append(f"step_{index:02d}/previous_output_input.txt")
                    if missing_steps:
                        classification = "incomplete"
                    else:
                        identity_checks = {
                            "model": EXPECTED["model"],
                            "model_digest": EXPECTED["model_digest"],
                            "seed": EXPECTED["seed"],
                            "temperature": EXPECTED["temperature"],
                            "num_ctx": EXPECTED["num_ctx"],
                            "max_output_tokens": EXPECTED["max_output_tokens"],
                            "experiment_config_hash": EXPECTED["experiment_config_hash"],
                            "prompt_hash": task["prompt_sha256"],
                            "dataset_id": task["dataset_id"],
                        }
                        for field, expected in identity_checks.items():
                            if generation.get(field) != expected:
                                stale_fields.append(field)
                        if generation.get("cache_key") != task["cache_key"] or status.get("cache_key") != task["cache_key"]:
                            stale_fields.append("cache_key")
                        actual_identity = generation.get("cache_identity") or {}
                        actual_cache_self_consistent = canonical_hash(actual_identity) == generation.get("cache_key") == status.get("cache_key")
                        integer_temperature_key = canonical_hash({**actual_identity, "temperature": 0}) if actual_identity else ""
                        if actual_cache_self_consistent and integer_temperature_key == task["cache_key"] and actual_identity.get("temperature") == 0.0:
                            cache_mismatch_cause = "canonical JSON numeric type: frozen plan used temperature integer 0; runtime envelope used float 0.0"
                        if canonical_hash(raw_records[task["dataset_id"]]) != task["dataset_record_sha256"]:
                            stale_fields.append("dataset_record_sha256")
                        actual_bundle = "\n\n".join(
                            f"# step_{index:02d}\n{str(call.get('assembled_prompt') or '')}"
                            for index, call in enumerate(calls, start=1)
                        )
                        actual_bundle_hash = hashlib.sha256(actual_bundle.encode("utf-8")).hexdigest()
                        actual_assembled_prompt_hash_self_consistent = actual_bundle_hash == generation.get("assembled_prompt_hash")
                        planned_calls = calls[:1] if task["method"] == "neon-gpt" else calls
                        planned_bundle = "\n\n".join(
                            f"# step_{index:02d}\n{str(call.get('assembled_prompt') or '')}"
                            for index, call in enumerate(planned_calls, start=1)
                        )
                        assembled_prompt_evidence_match = (
                            hashlib.sha256(planned_bundle.encode("utf-8")).hexdigest()
                            == task["assembled_prompt_bundle_sha256"]
                        )
                        if not actual_assembled_prompt_hash_self_consistent or not assembled_prompt_evidence_match:
                            stale_fields.append("assembled_prompt_bundle_sha256")
                        semantic_identity_values_match = set(stale_fields).issubset({"cache_key"})
                        if stale_fields:
                            classification = "stale_cache_mismatch"
                        elif status.get("status") == "success" and not error.get("error") and parse.get("final_parse_success") is True:
                            classification = "complete_success"
                        elif status.get("status") == "failed":
                            classification = "complete_failure"
                        else:
                            classification = "corrupt"
                            corrupt_files.append("inconsistent_status_parse_error_state")

        calls = list(((generation or {}).get("adapter_metadata") or {}).get("internal_calls") or [])
        for index, call in enumerate(calls, start=1):
            step_dir = directory / "steps" / f"step_{index:02d}"
            try:
                step_metadata = read_json(step_dir / "step_metadata.json")
                step_parse = read_json(step_dir / "parse_metadata.json")
                raw_response = read_json(step_dir / "raw_response.json")
                raw_output = (step_dir / "raw_output.txt").read_text(encoding="utf-8")
                final_output = (step_dir / "final_ontology.ttl").read_text(encoding="utf-8")
            except Exception as exc:
                telemetry_missing.append(f"step_{index:02d}:{exc}")
                continue
            telemetry = step_metadata.get("telemetry") or {}
            required_telemetry = ("prompt_eval_count", "eval_count", "done", "done_reason", "total_duration")
            absent_telemetry = [key for key in required_telemetry if telemetry.get(key) is None]
            if absent_telemetry:
                telemetry_missing.append(f"step_{index:02d}:{','.join(absent_telemetry)}")
            stage = str(step_metadata.get("pipeline_stage") or ("initial_generation" if task["method"] == "neon-gpt" else "cq_generation"))
            repair_call = stage not in {"initial_generation", "cq_generation"}
            attempts = int(step_metadata.get("attempts") or 0)
            raw_response_valid = isinstance(raw_response, dict) and bool(raw_response)
            raw_empty = not raw_output.strip()
            step_rows.append(
                {
                    "execution_order": task["execution_order"],
                    "task_id": task["task_id"],
                    "dataset_id": task["dataset_id"],
                    "method": task["method"],
                    "step": index,
                    "pipeline_stage": stage,
                    "repair_call": repair_call,
                    "http_attempts": attempts,
                    "transient_retries": max(0, attempts - 1),
                    "prompt_eval_count": telemetry.get("prompt_eval_count"),
                    "eval_count": telemetry.get("eval_count"),
                    "prompt_plus_completion": int(telemetry.get("prompt_eval_count") or 0) + int(telemetry.get("eval_count") or 0),
                    "arithmetic_margin_to_num_ctx": 32768 - int(telemetry.get("prompt_eval_count") or 0) - int(telemetry.get("eval_count") or 0),
                    "done": telemetry.get("done"),
                    "done_reason": telemetry.get("done_reason"),
                    "total_duration_seconds": round(int(telemetry.get("total_duration") or 0) / 1e9, 9),
                    "load_duration_seconds": round(int(telemetry.get("load_duration") or 0) / 1e9, 9),
                    "prompt_eval_duration_seconds": round(int(telemetry.get("prompt_eval_duration") or 0) / 1e9, 9),
                    "eval_duration_seconds": round(int(telemetry.get("eval_duration") or 0) / 1e9, 9),
                    "raw_response_present_and_object": raw_response_valid,
                    "raw_output_empty": raw_empty,
                    "raw_output_characters": len(raw_output),
                    "raw_ending": raw_output[-240:].replace("\r", "\\r").replace("\n", "\\n"),
                    "raw_parse_success": step_parse.get("raw_parse_success"),
                    "normalized_parse_success": step_parse.get("normalized_parse_success"),
                    "final_parse_success": step_parse.get("final_parse_success"),
                    "normalization_used": step_parse.get("normalization_used"),
                    "previous_output_required": "previous_output_input" in call,
                    "previous_output_present": (step_dir / "previous_output_input.txt").is_file(),
                    "telemetry_complete": not absent_telemetry,
                    "result_directory": task["output_directory"],
                }
            )

        task_step_rows = [row for row in step_rows if row["task_id"] == task["task_id"]]
        pipeline_repair_used = any(row["repair_call"] for row in task_step_rows)

        task_row = {
            "execution_order": task["execution_order"],
            "task_id": task["task_id"],
            "dataset_id": task["dataset_id"],
            "method": task["method"],
            "classification": classification,
            "status": (status or {}).get("status"),
            "cache_key_match": not stale_fields and classification != "missing",
            "actual_cache_self_consistent": actual_cache_self_consistent,
            "semantic_identity_values_match": semantic_identity_values_match,
            "cache_mismatch_cause": cache_mismatch_cause,
            "actual_assembled_prompt_hash_self_consistent": actual_assembled_prompt_hash_self_consistent,
            "planned_prompt_evidence_match": assembled_prompt_evidence_match,
            "top_level_files_complete": not missing_top and directory.exists(),
            "steps_complete": not missing_steps and directory.exists(),
            "telemetry_complete": not telemetry_missing and directory.exists(),
            "internal_call_count": len(calls),
            "http_attempt_count": sum(int(call.get("attempts") or 0) for call in calls),
            "missing_top_files": ";".join(missing_top),
            "missing_or_invalid_steps": ";".join(missing_steps),
            "corrupt_files": ";".join(corrupt_files),
            "stale_fields": ";".join(stale_fields),
            "telemetry_missing": ";".join(telemetry_missing),
            "execution_report_present": (task["dataset_id"], task["method"]) in execution_index,
            "execution_report_cache_status": (execution_index.get((task["dataset_id"], task["method"])) or {}).get("cache_status"),
            "output_path_collision": False,
            "result_directory": task["output_directory"],
        }
        completeness.append(task_row)

        task_parse = {
            "execution_order": task["execution_order"],
            "task_id": task["task_id"],
            "dataset_id": task["dataset_id"],
            "method": task["method"],
            "classification": classification,
            "raw_parse_success": (parse or {}).get("raw_parse_success"),
            "normalized_parse_success": (parse or {}).get("normalized_parse_success"),
            "final_parse_success": (parse or {}).get("final_parse_success"),
            "normalization_used": (parse or {}).get("normalization_used"),
            "repair_used": pipeline_repair_used,
            "repair_attempt_count": sum(row["repair_call"] for row in task_step_rows),
            "final_status": (status or {}).get("status"),
            "raw_parse_error": (parse or {}).get("raw_parse_error"),
            "normalized_parse_error": (parse or {}).get("normalized_parse_error"),
            "final_parse_error": (parse or {}).get("final_parse_error"),
            "internal_call_count": len(calls),
            "wall_clock_seconds": (generation or {}).get("wall_clock_seconds"),
            "prompt_tokens": (generation or {}).get("prompt_tokens"),
            "completion_tokens": (generation or {}).get("completion_tokens"),
        }
        parse_rows.append(task_parse)

        if classification != "complete_success":
            failure_rows.append(
                {
                    **{key: task_row[key] for key in ("execution_order", "task_id", "dataset_id", "method", "classification", "status")},
                    "error_category": (error or {}).get("category"),
                    "error": (error or {}).get("error"),
                    "final_parse_error": (parse or {}).get("final_parse_error"),
                    "internal_call_count": len(calls),
                    "result_directory": task["output_directory"],
                }
            )

        if task["method"] == "ontogenia" and directory.exists() and parse is not None:
            task_steps = sorted((row for row in step_rows if row["task_id"] == task["task_id"]), key=lambda row: int(row["step"]))
            cumulative = ""
            first_cumulative_failure = None
            cumulative_error = ""
            for row in task_steps:
                fragment = (directory / "steps" / f"step_{int(row['step']):02d}" / "normalized_output.txt").read_text(encoding="utf-8")
                cumulative = f"{cumulative}\n\n{fragment}" if cumulative else fragment
                ok, detail = parse_turtle(cumulative)
                if not ok and first_cumulative_failure is None:
                    first_cumulative_failure = int(row["step"])
                    cumulative_error = detail
            individual_failures = [int(row["step"]) for row in task_steps if row["final_parse_success"] is not True]
            origin = "none"
            category = "none"
            failing_content = ""
            if first_cumulative_failure is not None:
                failing_content = (directory / "steps" / f"step_{first_cumulative_failure:02d}" / "normalized_output.txt").read_text(encoding="utf-8")
                origin = "individual_output" if first_cumulative_failure in individual_failures else "cross_fragment"
                category = error_category(cumulative_error, failing_content)
            elif parse.get("final_parse_success") is not True:
                origin = "merged_output_only"
                category = error_category(str(parse.get("final_parse_error") or ""), (directory / "final_ontology.ttl").read_text(encoding="utf-8"))
            b6_rows.append(
                {
                    "execution_order": task["execution_order"],
                    "task_id": task["task_id"],
                    "dataset_id": task["dataset_id"],
                    "fragment_count": len(task_steps),
                    "individual_fragment_parse_success_count": len(task_steps) - len(individual_failures),
                    "individual_fragment_failure_count": len(individual_failures),
                    "individual_failing_fragments": ";".join(map(str, individual_failures)),
                    "merged_raw_parse_success": parse.get("raw_parse_success"),
                    "merged_normalized_parse_success": parse.get("normalized_parse_success"),
                    "merged_final_parse_success": parse.get("final_parse_success"),
                    "first_cumulative_failing_fragment": first_cumulative_failure,
                    "failure_origin": origin,
                    "syntax_error_category": category,
                    "first_failure_detail": cumulative_error[:1200],
                    "normalization_used": parse.get("normalization_used"),
                    "final_status": (status or {}).get("status"),
                    "result_directory": task["output_directory"],
                }
            )

        if parse is not None and parse.get("final_parse_success") is True:
            try:
                ontology = (directory / "final_ontology.ttl").read_text(encoding="utf-8")
                metric = compute_ontometrics(ontology, "ttl")
                metrics_rows.append(
                    {
                        "execution_order": task["execution_order"],
                        "task_id": task["task_id"],
                        "dataset_id": task["dataset_id"],
                        "method": task["method"],
                        "classification": classification,
                        **metric,
                    }
                )
            except Exception as exc:
                metric_failures.append(
                    {"task_id": task["task_id"], "dataset_id": task["dataset_id"], "method": task["method"], "reason": "metric_computation_failure", "detail": str(exc)}
                )
        else:
            metric_failures.append(
                {"task_id": task["task_id"], "dataset_id": task["dataset_id"], "method": task["method"], "reason": "final_ontology_not_parseable", "detail": (parse or {}).get("final_parse_error")}
            )

    if len({row["result_directory"] for row in completeness}) != len(completeness):
        raise RuntimeError("A1 task plan contains output path collisions")
    if len(execution_rows) != 51 or len(execution_index) != 51:
        raise RuntimeError(f"Execution report task reconciliation failed: rows={len(execution_rows)}, unique={len(execution_index)}")

    length_rows = [row for row in step_rows if row["done_reason"] == "length"]
    generation_summary: list[dict[str, Any]] = []
    runtime_summary: list[dict[str, Any]] = []
    for method in ("ontogenia", "domain-ontogen", "neon-gpt", "TOTAL"):
        tasks = parse_rows if method == "TOTAL" else [row for row in parse_rows if row["method"] == method]
        complete = completeness if method == "TOTAL" else [row for row in completeness if row["method"] == method]
        steps = step_rows if method == "TOTAL" else [row for row in step_rows if row["method"] == method]
        repairs = [row for row in steps if row["repair_call"]]
        classifications = Counter(row["classification"] for row in complete)
        summary = {
            "method": method,
            "planned_tasks": len(tasks),
            "execution_report_tasks": sum(row["execution_report_present"] for row in complete),
            "complete_success": classifications["complete_success"],
            "complete_failure": classifications["complete_failure"],
            "incomplete": classifications["incomplete"],
            "corrupt": classifications["corrupt"],
            "stale_cache_mismatch": classifications["stale_cache_mismatch"],
            "missing": classifications["missing"],
            "internal_calls": len(steps),
            "http_attempts": sum(int(row["http_attempts"]) for row in steps),
            "transient_retries": sum(int(row["transient_retries"]) for row in steps),
            "repair_calls": len(repairs),
            "done_reason_length": sum(row["done_reason"] == "length" for row in steps),
            "done_reason_stop": sum(row["done_reason"] == "stop" for row in steps),
            "malformed_or_empty_responses": sum(not row["raw_response_present_and_object"] or row["raw_output_empty"] for row in steps),
            "raw_parse_success": bool_count(tasks, "raw_parse_success"),
            "normalized_parse_success": bool_count(tasks, "normalized_parse_success"),
            "final_parse_success": bool_count(tasks, "final_parse_success"),
            "normalization_used": bool_count(tasks, "normalization_used"),
            "repair_used_tasks": bool_count(tasks, "repair_used"),
            "prompt_tokens": sum(int(row.get("prompt_eval_count") or 0) for row in steps),
            "completion_tokens": sum(int(row.get("eval_count") or 0) for row in steps),
            "call_duration_seconds": round(sum(float(row.get("total_duration_seconds") or 0) for row in steps), 6),
            "task_wall_clock_seconds": round(sum(float(row.get("wall_clock_seconds") or 0) for row in tasks), 6),
        }
        generation_summary.append(summary)
        runtime_summary.append(
            {
                "method": method,
                "tasks": len(tasks),
                "internal_calls": len(steps),
                "task_wall_clock_seconds": summary["task_wall_clock_seconds"],
                "call_total_duration_seconds": summary["call_duration_seconds"],
                "prompt_tokens": summary["prompt_tokens"],
                "completion_tokens": summary["completion_tokens"],
                "maximum_prompt_tokens": max((int(row.get("prompt_eval_count") or 0) for row in steps), default=0),
                "maximum_completion_tokens": max((int(row.get("eval_count") or 0) for row in steps), default=0),
                "maximum_prompt_plus_completion": max((int(row.get("prompt_plus_completion") or 0) for row in steps), default=0),
                "minimum_arithmetic_margin_to_32768": min((int(row.get("arithmetic_margin_to_num_ctx") or 0) for row in steps), default=32768),
            }
        )

    write_csv(RESULTS / "a1_generation_summary.csv", generation_summary)
    write_csv(RESULTS / "a1_failure_summary.csv", failure_rows)
    write_csv(RESULTS / "a1_parse_summary.csv", parse_rows)
    write_csv(RESULTS / "a1_runtime_summary.csv", runtime_summary)
    write_csv(RESULTS / "a1_task_completeness.csv", completeness)
    write_csv(RESULTS / "a1_step_telemetry.csv", step_rows)
    write_csv(RESULTS / "a1_length_terminations.csv", length_rows, list(step_rows[0].keys()) if step_rows else [])
    write_csv(RESULTS / "a1_b6_analysis.csv", b6_rows)
    write_csv(RESULTS / "a1_existing_structural_metrics.csv", metrics_rows)
    write_csv(RESULTS / "a1_existing_metric_failures.csv", metric_failures)

    starts = [datetime.fromisoformat(str(row.get("start_time")).replace("Z", "+00:00")) for row in (read_json(ROOT / task["output_directory"] / "generation_metadata.json") for task in plans) if row.get("start_time")]
    ends = [datetime.fromisoformat(str(row.get("end_time")).replace("Z", "+00:00")) for row in (read_json(ROOT / task["output_directory"] / "generation_metadata.json") for task in plans) if row.get("end_time")]
    total = generation_summary[-1]
    report_summary = {
        "execution_report_rows": len(execution_rows),
        "execution_report_mode": execution.get("mode"),
        "execution_config_hash": execution.get("config_hash"),
        "metadata_start_utc": min(starts).isoformat() if starts else None,
        "metadata_end_utc": max(ends).isoformat() if ends else None,
        "metadata_wall_clock_seconds": (max(ends) - min(starts)).total_seconds() if starts and ends else None,
        "generation_summary": total,
        "structural_metrics": {"computed": len(metrics_rows), "failures_or_unparseable": len(metric_failures), "coverage_denominator": 51},
        "b6": {
            "tasks": len(b6_rows),
            "fragments": sum(int(row["fragment_count"]) for row in b6_rows),
            "individual_fragment_failures": sum(int(row["individual_fragment_failure_count"]) for row in b6_rows),
            "merged_failures": sum(row["merged_final_parse_success"] is not True for row in b6_rows),
            "categories": dict(Counter(row["syntax_error_category"] for row in b6_rows if row["syntax_error_category"] != "none")),
            "origins": dict(Counter(row["failure_origin"] for row in b6_rows)),
        },
        "length": {
            "total": len(length_rows),
            "by_method": dict(Counter(row["method"] for row in length_rows)),
            "initial": sum(row["pipeline_stage"] in {"initial_generation", "cq_generation"} for row in length_rows),
            "repair": sum(row["repair_call"] for row in length_rows),
        },
        "accounting_disagreements": [],
        "proprietary_calls": 0 if all(row["method"] in {"ontogenia", "domain-ontogen", "neon-gpt"} for row in step_rows) else "unknown",
    }
    if total["planned_tasks"] != 51 or total["execution_report_tasks"] != 51:
        report_summary["accounting_disagreements"].append("top_level_task_count")
    if total["internal_calls"] != sum(row["internal_call_count"] for row in completeness):
        report_summary["accounting_disagreements"].append("internal_call_count")
    (RESULTS / "a1_analysis_summary.json").write_text(json.dumps(report_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report_summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
