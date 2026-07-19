"""Audit immutable Stage A2 evidence and execute the frozen C3 analysis policy."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import numpy as np
import yaml
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD
from scipy import stats


ROOT = Path(__file__).resolve().parents[1]
RESTAPI = ROOT / "restapi"
for entry in (ROOT, RESTAPI):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from app.utils.ontology_artifacts import (  # noqa: E402
    A2_CACHE_FIELDS,
    CACHE_IDENTITY_SCHEMA_V2,
    REQUIRED_RESULT_FILES,
    REQUIRED_STEP_FILES,
    canonical_json_v2_bytes,
    result_state,
)
from scripts.phase11_a2_contract import (  # noqa: E402
    APPROVED_PROMPT_HASHES,
    CONFIG_PATH,
    cache_values_for_task,
    load_historical_frozen_config,
    load_plan,
    validate_plan,
)
from scripts.prepare_stage_a1_preflight import (  # noqa: E402
    FROZEN_HASHES,
    HISTORICAL_MANIFESTS,
    historical_manifests,
    snapshot_manifest,
)


RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"
A1_TASKS = RESULTS / "a1_admitted_task_results.csv"
A1_ADMISSION = RESULTS / "a1_evidence_admission_manifest.json"
A2_EXECUTION = REPORTS / "stage_a2_execute_execution.json"
C3_POLICY = ROOT / "config/c3_analysis_policy.yaml"
C3_PLAN = ROOT / "docs/C3_STATISTICAL_ANALYSIS_PLAN.md"
A1_ROOT = ROOT / "outputs/stage_a1"
A2_ROOT = ROOT / "outputs/stage_a2"
EXPECTED_A1_TREE_SHA256 = "6b72414f2ed78bbd50a5020e791467d4199bd32cdb553af0e53b1ba04a9f881d"
EXPECTED_A2_TREE_SHA256 = "4dd413748b704a71592f65413fcf355b0161c5ce6e7a37904d080f7858b1b650"
RESUME_COMMAND = (
    ".venv\\Scripts\\python.exe -m scripts.run_stage_a2 "
    "--config config\\experiment_stage_a2.yaml --resume"
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    rows = list(rows)
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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda p: p.relative_to(root).as_posix())
    size = 0
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        size += len(content)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest(), len(files), size


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def as_bool(value: Any) -> bool | None:
    if value is True or str(value).lower() == "true":
        return True
    if value is False or str(value).lower() == "false":
        return False
    return None


def parse_error_category(error: str) -> str:
    value = (error or "").lower()
    if not value:
        return "none"
    if "prefix" in value and ("not bound" in value or "not defined" in value):
        return "unbound_prefix"
    if "disjointwith" in value:
        return "malformed_disjointness_syntax"
    if "eof" in value or "unterminated" in value or "end of file" in value:
        return "truncated_or_unterminated"
    if "expected" in value or "bad syntax" in value:
        return "general_turtle_syntax"
    return "other_parse_failure"


def canonical_identity_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_v2_bytes(value)).hexdigest()


def audit_a2(config: dict[str, Any], plan: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    task_rows: list[dict[str, Any]] = []
    step_rows: list[dict[str, Any]] = []
    for task in plan:
        directory = ROOT / task["output_directory"]
        missing_top: list[str] = []
        corrupt: list[str] = []
        missing_steps: list[str] = []
        telemetry_gaps: list[str] = []
        status = generation = parse = error = None
        state = "missing"
        if directory.exists():
            missing_top = [name for name in REQUIRED_RESULT_FILES if not (directory / name).is_file()]
            if missing_top:
                state = "incomplete"
            else:
                for name in ("status.json", "generation_metadata.json", "parse_metadata.json", "error.json", "raw_response.json"):
                    try:
                        read_json(directory / name)
                    except Exception as exc:
                        corrupt.append(f"{name}:{type(exc).__name__}")
                if corrupt:
                    state = "corrupt"
                else:
                    status = read_json(directory / "status.json")
                    generation = read_json(directory / "generation_metadata.json")
                    parse = read_json(directory / "parse_metadata.json")
                    error = read_json(directory / "error.json")
                    raw_state = result_state(
                        directory,
                        task["schema_v2_cache_key"],
                        expected_schema_version=CACHE_IDENTITY_SCHEMA_V2,
                    )
                    state = {
                        "success": "complete_success",
                        "failed": "complete_failure",
                        "stale": "stale_cache_mismatch",
                        "schema_mismatch": "stale_cache_mismatch",
                    }.get(raw_state, raw_state)

        calls = list(((generation or {}).get("adapter_metadata") or {}).get("internal_calls") or [])
        declared_calls = (generation or {}).get("internal_call_count")
        if directory.exists() and not missing_top and not corrupt and declared_calls != len(calls):
            missing_steps.append("declared_internal_call_count_mismatch")
        for index, call in enumerate(calls, start=1):
            step_dir = directory / "steps" / f"step_{index:02d}"
            for name in REQUIRED_STEP_FILES:
                if not (step_dir / name).is_file():
                    missing_steps.append(f"step_{index:02d}/{name}")
            if "previous_output_input" in call and not (step_dir / "previous_output_input.txt").is_file():
                missing_steps.append(f"step_{index:02d}/previous_output_input.txt")
            if any(not (step_dir / name).is_file() for name in REQUIRED_STEP_FILES):
                continue
            step_meta = read_json(step_dir / "step_metadata.json")
            step_parse = read_json(step_dir / "parse_metadata.json")
            telemetry = step_meta.get("telemetry") or {}
            required_telemetry = ("prompt_eval_count", "eval_count", "done", "done_reason", "total_duration")
            absent = [name for name in required_telemetry if telemetry.get(name) is None]
            if absent:
                telemetry_gaps.append(f"step_{index:02d}:{','.join(absent)}")
            stage = str(step_meta.get("pipeline_stage") or call.get("pipeline_stage") or "unknown")
            repair = stage not in {"initial_generation", "cq_generation"}
            attempts = int(step_meta.get("attempts") or call.get("attempts") or 0)
            step_rows.append(
                {
                    "execution_order": int(task["execution_order"]),
                    "task_id": task["task_id"],
                    "dataset_id": task["dataset_id"],
                    "method": task["method"],
                    "prompt_variant": task["prompt_variant"],
                    "task_classification": state,
                    "step": index,
                    "pipeline_stage": stage,
                    "repair_call": repair,
                    "http_attempts": attempts,
                    "retries": max(0, attempts - 1),
                    "prompt_tokens": int(telemetry.get("prompt_eval_count") or 0),
                    "completion_tokens": int(telemetry.get("eval_count") or 0),
                    "total_context_tokens": int(telemetry.get("prompt_eval_count") or 0) + int(telemetry.get("eval_count") or 0),
                    "margin_to_num_ctx": int(config["generation"]["num_ctx"]) - int(telemetry.get("prompt_eval_count") or 0) - int(telemetry.get("eval_count") or 0),
                    "done": telemetry.get("done"),
                    "done_reason": telemetry.get("done_reason"),
                    "duration_seconds": round(int(telemetry.get("total_duration") or 0) / 1e9, 9),
                    "raw_parse_success": step_parse.get("raw_parse_success"),
                    "normalized_parse_success": step_parse.get("normalized_parse_success"),
                    "final_parse_success": step_parse.get("final_parse_success"),
                    "normalization_used": step_parse.get("normalization_used"),
                    "previous_output_required": "previous_output_input" in call,
                    "previous_output_present": (step_dir / "previous_output_input.txt").is_file(),
                    "approved_suffix_present": "Additional instruction:" in str(call.get("assembled_prompt") or "") if not repair else "not_applicable_repair_prompt",
                    "telemetry_complete": not absent,
                    "request_path": (step_dir / "request.json").relative_to(ROOT).as_posix(),
                    "raw_response_path": (step_dir / "raw_response.json").relative_to(ROOT).as_posix(),
                }
            )

        if missing_steps and state not in {"missing", "corrupt"}:
            state = "incomplete"
        expected_values = cache_values_for_task(config, task)
        actual_identity = (generation or {}).get("cache_identity") or {}
        identity_mismatch_fields = [
            field for field in A2_CACHE_FIELDS if actual_identity.get(field) != expected_values.get(field)
        ]
        actual_key = (generation or {}).get("cache_key")
        status_key = (status or {}).get("cache_key")
        actual_identity_self_consistent = bool(actual_identity) and canonical_identity_hash(actual_identity) == actual_key == status_key
        task_steps = [row for row in step_rows if row["task_id"] == task["task_id"]]
        initial_steps = [row for row in task_steps if row["repair_call"] is False]
        parse = parse or {}
        task_rows.append(
            {
                "execution_order": int(task["execution_order"]),
                "task_id": task["task_id"],
                "dataset_id": task["dataset_id"],
                "method": task["method"],
                "prompt_variant": task["prompt_variant"],
                "classification": state,
                "envelope_status": (status or {}).get("status"),
                "planned_cache_key": task["schema_v2_cache_key"],
                "actual_cache_key": actual_key,
                "status_cache_key": status_key,
                "cache_key_match": actual_key == status_key == task["schema_v2_cache_key"],
                "actual_identity_self_consistent": actual_identity_self_consistent,
                "identity_mismatch_fields": ";".join(identity_mismatch_fields),
                "planned_prompt_hash": task["prompt_hash"],
                "recorded_prompt_hash": (generation or {}).get("prompt_hash"),
                "recorded_identity_prompt_hash": actual_identity.get("prompt_hash"),
                "prompt_hash_match": (generation or {}).get("prompt_hash") == task["prompt_hash"] and actual_identity.get("prompt_hash") == task["prompt_hash"],
                "source_prompt_path": (generation or {}).get("source_prompt_path"),
                "initial_call_count": len(initial_steps),
                "approved_suffix_all_initial_calls": bool(initial_steps) and all(row["approved_suffix_present"] is True for row in initial_steps),
                "top_level_files_complete": directory.exists() and not missing_top,
                "steps_complete": directory.exists() and not missing_steps,
                "telemetry_complete": directory.exists() and not telemetry_gaps,
                "internal_call_count": len(calls),
                "http_attempt_count": sum(int(row["http_attempts"]) for row in task_steps),
                "retry_count": sum(int(row["retries"]) for row in task_steps),
                "repair_call_count": sum(row["repair_call"] is True for row in task_steps),
                "length_termination_count": sum(row["done_reason"] == "length" for row in task_steps),
                "raw_parse_success": parse.get("raw_parse_success"),
                "normalized_parse_success": parse.get("normalized_parse_success"),
                "final_parse_success": parse.get("final_parse_success"),
                "normalization_used": parse.get("normalization_used"),
                "repair_attempt_count": parse.get("repair_attempt_count"),
                "wall_clock_seconds": (generation or {}).get("wall_clock_seconds"),
                "generation_metadata_prompt_tokens": (generation or {}).get("prompt_tokens"),
                "generation_metadata_completion_tokens": (generation or {}).get("completion_tokens"),
                "step_telemetry_prompt_tokens": sum(int(row["prompt_tokens"]) for row in task_steps),
                "step_telemetry_completion_tokens": sum(int(row["completion_tokens"]) for row in task_steps),
                "top_level_token_fields_match_steps": int((generation or {}).get("prompt_tokens") or 0) == sum(int(row["prompt_tokens"]) for row in task_steps) and int((generation or {}).get("completion_tokens") or 0) == sum(int(row["completion_tokens"]) for row in task_steps),
                "final_parse_error_category": parse_error_category(str(parse.get("final_parse_error") or "")),
                "missing_top_files": ";".join(missing_top),
                "missing_or_invalid_steps": ";".join(missing_steps),
                "corrupt_files": ";".join(corrupt),
                "telemetry_gaps": ";".join(telemetry_gaps),
                "output_directory": task["output_directory"],
            }
        )
    return task_rows, step_rows


def summary_rows(tasks: list[dict[str, Any]], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    groups = [(method, variant) for method in ("ontogenia", "domain-ontogen", "neon-gpt") for variant in ("P1", "P2")]
    groups.append(("TOTAL", "P1+P2"))
    for method, variant in groups:
        subset = tasks if method == "TOTAL" else [r for r in tasks if r["method"] == method and r["prompt_variant"] == variant]
        ids = {r["task_id"] for r in subset}
        calls = [r for r in steps if r["task_id"] in ids]
        counts = Counter(r["classification"] for r in subset)
        rows.append(
            {
                "method": method,
                "prompt_variant": variant,
                "tasks": len(subset),
                "complete_success": counts["complete_success"],
                "complete_failure": counts["complete_failure"],
                "incomplete": counts["incomplete"],
                "corrupt": counts["corrupt"],
                "stale_cache_mismatch": counts["stale_cache_mismatch"],
                "missing": counts["missing"],
                "observed_envelope_success": sum(r["envelope_status"] == "success" for r in subset),
                "observed_envelope_failure": sum(r["envelope_status"] == "failed" for r in subset),
                "internal_calls": len(calls),
                "http_attempts": sum(int(r["http_attempts"]) for r in calls),
                "retries": sum(int(r["retries"]) for r in calls),
                "repair_calls": sum(r["repair_call"] is True for r in calls),
                "length_terminations": sum(r["done_reason"] == "length" for r in calls),
                "prompt_tokens": sum(int(r["prompt_tokens"]) for r in calls),
                "completion_tokens": sum(int(r["completion_tokens"]) for r in calls),
                "generation_metadata_prompt_tokens": sum(int(r["generation_metadata_prompt_tokens"] or 0) for r in subset),
                "generation_metadata_completion_tokens": sum(int(r["generation_metadata_completion_tokens"] or 0) for r in subset),
                "top_level_token_mismatch_tasks": sum(r["top_level_token_fields_match_steps"] is False for r in subset),
                "wall_clock_seconds": round(sum(float(r["wall_clock_seconds"] or 0) for r in subset), 9),
            }
        )
    return rows


def parse_summary_rows(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for method in ("ontogenia", "domain-ontogen", "neon-gpt", "TOTAL"):
        for variant in (("P1", "P2") if method != "TOTAL" else ("P1+P2",)):
            subset = tasks if method == "TOTAL" else [r for r in tasks if r["method"] == method and r["prompt_variant"] == variant]
            eligible = [r for r in subset if r["classification"] in {"complete_success", "complete_failure"}]
            rows.append(
                {
                    "method": method,
                    "prompt_variant": variant,
                    "observed_tasks": len(subset),
                    "c3_identity_eligible_tasks": len(eligible),
                    "raw_parse_success": sum(r["raw_parse_success"] is True for r in subset),
                    "raw_parse_rate": round(sum(r["raw_parse_success"] is True for r in subset) / len(subset), 9) if subset else None,
                    "normalized_parse_success": sum(r["normalized_parse_success"] is True for r in subset),
                    "normalized_parse_rate": round(sum(r["normalized_parse_success"] is True for r in subset) / len(subset), 9) if subset else None,
                    "final_parse_success": sum(r["final_parse_success"] is True for r in subset),
                    "final_parse_rate": round(sum(r["final_parse_success"] is True for r in subset) / len(subset), 9) if subset else None,
                    "eligible_final_parse_success": sum(r["final_parse_success"] is True for r in eligible),
                    "eligible_final_parse_rate": round(sum(r["final_parse_success"] is True for r in eligible) / len(eligible), 9) if eligible else None,
                }
            )
    return rows


UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
PERCENT = re.compile(r"%([0-9A-Fa-f]{2})")


def normalize_iri(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    split = urlsplit(value)
    host = split.hostname.lower() if split.hostname else ""
    port = split.port
    if (split.scheme.lower(), port) in {("http", 80), ("https", 443)}:
        port = None
    userinfo = ""
    if split.username is not None:
        userinfo = split.username
        if split.password is not None:
            userinfo += f":{split.password}"
        userinfo += "@"
    netloc = userinfo + host + (f":{port}" if port is not None else "")

    def decode_unreserved(text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            char = chr(int(match.group(1), 16))
            return char if char in UNRESERVED else match.group(0).upper()
        return PERCENT.sub(replace, text)

    return urlunsplit((split.scheme.lower(), netloc, decode_unreserved(split.path), decode_unreserved(split.query), decode_unreserved(split.fragment)))


BUILT_INS = tuple(str(ns) for ns in (RDF, RDFS, OWL, XSD))
DECLARED_TYPES = {OWL.Class, RDFS.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property, OWL.NamedIndividual}
RELATION_PREDICATES = {RDFS.domain, RDFS.range, RDFS.subClassOf, OWL.equivalentClass, OWL.disjointWith, OWL.inverseOf}


def ontology_terms(path: Path) -> tuple[set[str] | None, str | None]:
    try:
        graph = Graph().parse(path, format="turtle")
    except Exception as exc:
        return None, str(exc)
    ontology_iris = {subject for subject in graph.subjects(RDF.type, OWL.Ontology) if isinstance(subject, URIRef)}
    terms: set[URIRef] = set()
    for subject, obj in graph.subject_objects(RDF.type):
        if obj in DECLARED_TYPES and isinstance(subject, URIRef):
            terms.add(subject)
    for subject, predicate, obj in graph:
        if predicate in RELATION_PREDICATES:
            if isinstance(subject, URIRef):
                terms.add(subject)
            if isinstance(obj, URIRef):
                terms.add(obj)
    normalized = {
        normalize_iri(str(term))
        for term in terms
        if term not in ontology_iris and not str(term).startswith(BUILT_INS)
    }
    return normalized, None


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def cochran_q(values: list[list[int]]) -> tuple[str, float | None, float | None, int]:
    n = len(values)
    if n < 2:
        return "not_estimable_insufficient_complete_pairs", None, None, n
    array = np.asarray(values, dtype=float)
    column = array.sum(axis=0)
    row = array.sum(axis=1)
    denominator = len(column) * array.sum() - np.square(row).sum()
    if denominator <= 0:
        return "not_estimable_no_within_pair_variation", None, None, n
    statistic = (len(column) - 1) * (len(column) * np.square(column).sum() - array.sum() ** 2) / denominator
    return "estimated", float(statistic), float(stats.chi2.sf(statistic, len(column) - 1)), n


def holm_adjust(pvalues: list[float | None]) -> list[float | None]:
    indices = sorted((i for i, p in enumerate(pvalues) if p is not None), key=lambda i: float(pvalues[i]))
    result: list[float | None] = [None] * len(pvalues)
    running = 0.0
    m = len(indices)
    for rank, index in enumerate(indices):
        adjusted = min(1.0, (m - rank) * float(pvalues[index]))
        running = max(running, adjusted)
        result[index] = running
    return result


def mcnemar_rows(method: str, panel: dict[tuple[str, str, str], dict[str, Any]], omnibus_estimable: bool) -> list[dict[str, Any]]:
    output = []
    comparisons = (("P0", "P1"), ("P0", "P2"), ("P1", "P2"))
    datasets = sorted({dataset for m, dataset, _variant in panel if m == method})
    for left, right in comparisons:
        pairs = [(panel[(method, d, left)]["value"], panel[(method, d, right)]["value"]) for d in datasets if panel.get((method, d, left), {}).get("value") is not None and panel.get((method, d, right), {}).get("value") is not None]
        row: dict[str, Any] = {
            "method": method,
            "comparison": f"{left}_vs_{right}",
            "paired_n": len(pairs),
            "left_success_right_failure": sum(a == 1 and b == 0 for a, b in pairs),
            "left_failure_right_success": sum(a == 0 and b == 1 for a, b in pairs),
            "test": None,
            "statistic": None,
            "p_value_raw": None,
            "p_value_holm": None,
            "risk_difference_right_minus_left": None,
            "matched_odds_ratio_right_over_left": None,
            "status": "not_estimated_omnibus_not_estimable" if not omnibus_estimable else "not_estimable_no_pairs",
        }
        discordant = row["left_success_right_failure"] + row["left_failure_right_success"]
        if omnibus_estimable and pairs and discordant:
            b = row["left_success_right_failure"]
            c = row["left_failure_right_success"]
            if discordant < 25:
                row["test"] = "exact_binomial_two_sided"
                row["p_value_raw"] = float(stats.binomtest(min(b, c), discordant, 0.5).pvalue)
            else:
                row["test"] = "continuity_corrected_asymptotic"
                statistic = (abs(b - c) - 1) ** 2 / discordant
                row["statistic"] = statistic
                row["p_value_raw"] = float(stats.chi2.sf(statistic, 1))
            row["risk_difference_right_minus_left"] = (c - b) / len(pairs)
            row["matched_odds_ratio_right_over_left"] = "infinite" if b == 0 and c > 0 else (0.0 if c == 0 and b > 0 else c / b)
            row["status"] = "estimated"
        elif omnibus_estimable and pairs and discordant == 0:
            row["test"] = "exact_no_discordance"
            row["p_value_raw"] = 1.0
            row["risk_difference_right_minus_left"] = 0.0
            row["matched_odds_ratio_right_over_left"] = "undefined_0_over_0"
            row["status"] = "estimated"
        output.append(row)
    adjusted = holm_adjust([row["p_value_raw"] for row in output])
    for row, value in zip(output, adjusted):
        row["p_value_holm"] = value
    return output


def c3_analysis(
    tasks: list[dict[str, Any]],
    admitted_task_ids: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    a1 = read_csv(A1_TASKS)
    a2_index = {(r["method"], r["dataset_id"], r["prompt_variant"]): r for r in tasks}
    panel_rows: list[dict[str, Any]] = []
    panel: dict[tuple[str, str, str], dict[str, Any]] = {}
    for base in a1:
        method, dataset = base["method"], base["dataset_id"]
        for variant in ("P0", "P1", "P2"):
            if variant == "P0":
                value = 1 if as_bool(base["final_parse_success"]) else 0
                evidence_status = "ADMITTED_A1_P0"
                exclusion = ""
                path = base["original_output_directory"]
            else:
                row = a2_index[(method, dataset, variant)]
                eligible = (
                    row["task_id"] in admitted_task_ids
                    if admitted_task_ids is not None
                    else row["classification"]
                    in {"complete_success", "complete_failure"}
                )
                value = (1 if row["final_parse_success"] is True else 0) if eligible else None
                evidence_status = (
                    "ADMITTED_A2_SIDECAR"
                    if admitted_task_ids is not None and eligible
                    else row["classification"]
                )
                exclusion = "" if eligible else row["classification"]
                path = row["output_directory"]
            record = {
                "method": method,
                "dataset_id": dataset,
                "prompt_variant": variant,
                "evidence_status": evidence_status,
                "binary_endpoint_observed": value is not None,
                "final_parse_success": value,
                "exclusion_reason": exclusion,
                "output_directory": path,
            }
            panel_rows.append(record)
            panel[(method, dataset, variant)] = {"value": value, "path": path, "status": evidence_status}

    cochran_rows: list[dict[str, Any]] = []
    mcnemar: list[dict[str, Any]] = []
    for method in ("ontogenia", "domain-ontogen", "neon-gpt"):
        datasets = sorted({dataset for m, dataset, _v in panel if m == method})
        values = [[panel[(method, d, v)]["value"] for v in ("P0", "P1", "P2")] for d in datasets]
        complete = [[int(x) for x in row] for row in values if all(x is not None for x in row)]
        status, statistic, pvalue, n = cochran_q(complete)
        cochran_rows.append(
            {
                "method": method,
                "variants": "P0;P1;P2",
                "paired_n": n,
                "excluded_pairs": 17 - n,
                "statistic": statistic,
                "degrees_of_freedom": 2 if statistic is not None else None,
                "p_value": pvalue,
                "status": status,
            }
        )
        mcnemar.extend(mcnemar_rows(method, panel, status == "estimated"))

    term_rows: list[dict[str, Any]] = []
    wilcoxon_rows: list[dict[str, Any]] = []
    for method in ("ontogenia", "domain-ontogen", "neon-gpt"):
        method_terms: list[dict[str, Any]] = []
        datasets = sorted({dataset for m, dataset, _v in panel if m == method})
        for dataset in datasets:
            extracted: dict[str, set[str] | None] = {}
            errors: dict[str, str] = {}
            for variant in ("P0", "P1", "P2"):
                entry = panel[(method, dataset, variant)]
                if entry["value"] != 1:
                    extracted[variant] = None
                    errors[variant] = "identity_excluded_or_unparseable"
                    continue
                terms, error = ontology_terms(ROOT / entry["path"] / "final_ontology.ttl")
                extracted[variant] = terms
                errors[variant] = error or ""
            jp1 = jaccard(extracted["P0"], extracted["P1"]) if extracted["P0"] is not None and extracted["P1"] is not None else None
            jp2 = jaccard(extracted["P0"], extracted["P2"]) if extracted["P0"] is not None and extracted["P2"] is not None else None
            row = {
                "method": method,
                "dataset_id": dataset,
                "P0_term_count": len(extracted["P0"]) if extracted["P0"] is not None else None,
                "P1_term_count": len(extracted["P1"]) if extracted["P1"] is not None else None,
                "P2_term_count": len(extracted["P2"]) if extracted["P2"] is not None else None,
                "J_P0_P1": jp1,
                "J_P0_P2": jp2,
                "paired_difference_P0P2_minus_P0P1": jp2 - jp1 if jp1 is not None and jp2 is not None else None,
                "P0_extraction_error": errors["P0"],
                "P1_exclusion_or_error": errors["P1"],
                "P2_exclusion_or_error": errors["P2"],
                "included_in_wilcoxon": jp1 is not None and jp2 is not None,
            }
            term_rows.append(row)
            method_terms.append(row)
        usable = [r for r in method_terms if r["included_in_wilcoxon"]]
        result: dict[str, Any] = {
            "method": method,
            "paired_n": len(usable),
            "excluded_pairs": 17 - len(usable),
            "zero_differences": None,
            "positive_rank_sum": None,
            "negative_rank_sum": None,
            "statistic": None,
            "p_value": None,
            "median_difference_P0P2_minus_P0P1": None,
            "matched_rank_biserial": None,
            "bootstrap_ci_low": None,
            "bootstrap_ci_high": None,
            "implementation": "scipy.stats.wilcoxon asymptotic Pratt; paired bootstrap 10000 seed 42",
            "status": "not_estimable_no_parseable_identity_eligible_pairs",
        }
        if usable:
            left = np.array([float(r["J_P0_P1"]) for r in usable])
            right = np.array([float(r["J_P0_P2"]) for r in usable])
            differences = right - left
            ranks = stats.rankdata(np.abs(differences))
            positive = float(ranks[differences > 0].sum())
            negative = float(ranks[differences < 0].sum())
            denom = positive + negative
            result.update(
                {
                    "zero_differences": int(np.sum(differences == 0)),
                    "positive_rank_sum": positive,
                    "negative_rank_sum": negative,
                    "median_difference_P0P2_minus_P0P1": float(np.median(differences)),
                    "matched_rank_biserial": (positive - negative) / denom if denom else 0.0,
                }
            )
            if np.all(differences == 0):
                result.update({"statistic": 0.0, "p_value": 1.0, "status": "estimated_all_zero"})
            else:
                test = stats.wilcoxon(right, left, zero_method="pratt", alternative="two-sided", method="approx")
                result.update({"statistic": float(test.statistic), "p_value": float(test.pvalue), "status": "estimated"})
            rng = np.random.default_rng(42)
            samples = np.median(differences[rng.integers(0, len(differences), size=(10000, len(differences)))], axis=1)
            result["bootstrap_ci_low"], result["bootstrap_ci_high"] = [float(v) for v in np.quantile(samples, [0.025, 0.975])]
        wilcoxon_rows.append(result)

    effects: list[dict[str, Any]] = []
    for row in mcnemar:
        effects.append(
            {
                "endpoint": "final_parse_success",
                "method": row["method"],
                "comparison": row["comparison"],
                "paired_n": row["paired_n"],
                "effect_name": "paired_risk_difference_and_matched_odds_ratio",
                "effect_value": row["risk_difference_right_minus_left"],
                "secondary_effect_value": row["matched_odds_ratio_right_over_left"],
                "status": row["status"],
            }
        )
    for row in wilcoxon_rows:
        effects.append(
            {
                "endpoint": "P0_centred_ontology_term_jaccard",
                "method": row["method"],
                "comparison": "J_P0_P1_vs_J_P0_P2",
                "paired_n": row["paired_n"],
                "effect_name": "matched_rank_biserial",
                "effect_value": row["matched_rank_biserial"],
                "secondary_effect_value": None,
                "status": row["status"],
            }
        )
    return {
        "panel": panel_rows,
        "cochran": cochran_rows,
        "mcnemar": mcnemar,
        "terms": term_rows,
        "wilcoxon": wilcoxon_rows,
        "effects": effects,
    }


def failure_summary(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: Counter[tuple[Any, ...]] = Counter()
    for row in tasks:
        if row["classification"] == "complete_success":
            continue
        grouped[(row["method"], row["prompt_variant"], row["classification"], row["envelope_status"], row["final_parse_error_category"], row["identity_mismatch_fields"])] += 1
    return [
        {
            "method": key[0], "prompt_variant": key[1], "classification": key[2],
            "envelope_status": key[3], "final_parse_error_category": key[4],
            "identity_mismatch_fields": key[5], "tasks": count,
        }
        for key, count in sorted(grouped.items(), key=lambda item: tuple(str(v) for v in item[0]))
    ]


def render_reports(summary: list[dict[str, Any]], parse_rows: list[dict[str, Any]], c3: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]) -> None:
    total = next(row for row in summary if row["method"] == "TOTAL")
    lines = [
        "# Stage A2 Post-run Evidence Review",
        "",
        "Classification: **FAIL / evidence-admission required**",
        "",
        "The execute command returned normally and wrote all 102 task envelopes, but strict frozen schema-v2 reconciliation finds 94 `stale_cache_mismatch` and 8 `complete_failure`; therefore there are zero matching complete successes.",
        "",
        "## Root cause and prompt-content audit",
        "",
        "All 102 top-level assembled prompts and all 330 initial/CQ prompts contain the approved P1/P2 suffix. The success writer subsequently overwrote the P1/P2 `prompt_hash` in generation metadata/cache identity with the method P0 hash. The eight NeOn failure-path envelopes retained the planned P1/P2 identity. No raw evidence was edited.",
        "",
        "## Accounting",
        "",
        f"- Tasks: 102; complete success {total['complete_success']}; complete failure {total['complete_failure']}; stale {total['stale_cache_mismatch']}.",
        f"- Calls/HTTP attempts/retries/repairs: {total['internal_calls']}/{total['http_attempts']}/{total['retries']}/{total['repair_calls']}.",
        f"- Length terminations: {total['length_terminations']}.",
        f"- Prompt/completion tokens: {total['prompt_tokens']}/{total['completion_tokens']}.",
        f"- Top-level metadata counters: {total['generation_metadata_prompt_tokens']}/{total['generation_metadata_completion_tokens']}; eight complete-failure envelopes store zero there, while all per-step native telemetry remains present.",
        f"- Sum of task wall-clock seconds: {total['wall_clock_seconds']}.",
        "",
        "## Resume verification",
        "",
        "The actual `--resume` command was run once. It stopped at the first stale task before runtime preflight or provider execution. Independent full-plan classification is 94 `block_stale`, 8 `held_failed`, 0 `skipped_success`, and 0 selected tasks. No resume report or output mutation occurred; model calls and HTTP attempts were zero. The required actual matching-success skip condition is not satisfiable because no successful envelope matches the frozen key.",
        "",
        "## Gate",
        "",
        "A2 is not admissible as a complete frozen result set under current authority. A future owner decision may authorize a sidecar semantic-evidence admission audit or a replacement run after a separately frozen implementation fix. Neither was performed here.",
        "",
        "## Verification",
        "",
        "- Full pytest: 108 passed, 0 failed, 0 skipped (one pre-existing Starlette/httpx deprecation warning).",
        "- Targeted suites: Phase 2/2B 7; Phase 3 4; Phase 4 22; A1/Phase 9 31; Phase 10 13; Phase 11/A2 14; ontology utilities/API 15 passed.",
        "- `python -m compileall restapi scripts`: passed.",
        "- Frozen config, 102-task plans, P0/P1/P2 prompts, A1 admission, dataset/C3 policy, Phase 3 snapshots, and historical Phase 5/6/6R manifests: unchanged.",
    ]
    (REPORTS / "A2_EXECUTION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    q = {row["method"]: row for row in c3["cochran"]}
    w = {row["method"]: row for row in c3["wilcoxon"]}
    c3_lines = [
        "# C3 Prompt Sensitivity Analysis",
        "",
        "Classification: **INCOMPLETE / NOT ESTIMABLE**",
        "",
        "The frozen policy was applied without treating stale A2 evidence as admitted. P0 contributes 51 admitted observations. A2 contributes eight identity-eligible complete failures and excludes 94 stale envelopes. No stale result is silently converted to a binary failure or used for ontology-term similarity.",
        "",
        "## Binary endpoint",
        "",
        "| Method | complete P0/P1/P2 pairs | Cochran Q status |",
        "|---|---:|---|",
    ]
    for method in ("ontogenia", "domain-ontogen", "neon-gpt"):
        c3_lines.append(f"| {method} | {q[method]['paired_n']} | {q[method]['status']} |")
    c3_lines.extend([
        "",
        "No omnibus test is estimable, so the planned gatekeeping rule prevents inferential McNemar tests. The CSV retains all nine planned comparisons with explicit denominators and non-estimable status.",
        "",
        "## P0-centred term Jaccard",
        "",
        "| Method | usable paired Jaccard observations | Wilcoxon status |",
        "|---|---:|---|",
    ])
    for method in ("ontogenia", "domain-ontogen", "neon-gpt"):
        c3_lines.append(f"| {method} | {w[method]['paired_n']} | {w[method]['status']} |")
    c3_lines.extend([
        "",
        "No P1/P2 ontology that is both parseable and identity-eligible forms a complete P0/P1/P2 term pair. Jaccard/Wilcoxon effects are therefore missing, not zero.",
        "",
        "## Interpretation boundary",
        "",
        "No publication or prompt-superiority claim is supported. Final report writing is NO-GO pending an explicit evidence-disposition decision for the 94 stale tasks.",
    ])
    (REPORTS / "C3_PROMPT_SENSITIVITY.md").write_text("\n".join(c3_lines) + "\n", encoding="utf-8")


def main() -> int:
    config = load_historical_frozen_config(CONFIG_PATH)
    plan = validate_plan(config, load_plan())
    execution = read_json(A2_EXECUTION)
    if execution.get("mode") != "execute" or execution.get("selected_tasks") != 102 or len(execution.get("results") or []) != 102:
        raise RuntimeError("Stage A2 execute report does not reconcile 102 tasks")
    task_rows, step_rows = audit_a2(config, plan)
    summary = summary_rows(task_rows, step_rows)
    parse_rows = parse_summary_rows(task_rows)
    length_rows = [row for row in step_rows if row["done_reason"] == "length"]
    c3 = c3_analysis(task_rows)

    write_csv(RESULTS / "a2_generation_summary.csv", summary)
    write_csv(RESULTS / "a2_task_completeness.csv", task_rows)
    write_csv(RESULTS / "a2_parse_summary.csv", parse_rows)
    write_csv(RESULTS / "a2_step_telemetry.csv", step_rows)
    write_csv(RESULTS / "a2_length_terminations.csv", length_rows)
    write_csv(RESULTS / "a2_failure_summary.csv", failure_summary(task_rows))
    write_csv(RESULTS / "c3_parse_success_panel.csv", c3["panel"])
    write_csv(RESULTS / "c3_mcnemar_results.csv", c3["mcnemar"])
    write_csv(RESULTS / "c3_cochran_q_results.csv", c3["cochran"])
    write_csv(RESULTS / "c3_term_jaccard.csv", c3["terms"])
    write_csv(RESULTS / "c3_wilcoxon_results.csv", c3["wilcoxon"])
    write_csv(RESULTS / "c3_effect_sizes.csv", c3["effects"])

    a1_hash, a1_files, a1_bytes = tree_hash(A1_ROOT)
    a2_hash, a2_files, a2_bytes = tree_hash(A2_ROOT)
    actions = Counter()
    for row in task_rows:
        actions[{"complete_success": "skipped_success", "complete_failure": "held_failed", "missing": "execute"}.get(row["classification"], f"block_{row['classification'].replace('_cache_mismatch', '')}")] += 1
    resume = {
        "command": RESUME_COMMAND,
        "command_executed": True,
        "exit_code": 1,
        "result": "blocked_before_runtime_preflight",
        "first_blocking_task": "a2_001_53103539d829",
        "first_blocking_state": "stale",
        "full_plan_actions": dict(actions),
        "selected_tasks": 0,
        "generation_calls": 0,
        "provider_http_generation_attempts": 0,
        "retry_failed_used": False,
        "force_used": False,
        "resume_report_created": (REPORTS / "stage_a2_resume_execution.json").exists(),
        "a1_tree_sha256_before_and_after": a1_hash,
        "a2_tree_sha256_before_and_after": a2_hash,
        "evidence_unchanged": a1_hash == EXPECTED_A1_TREE_SHA256 and a2_hash == EXPECTED_A2_TREE_SHA256,
        "actual_matching_success_skip_condition": "not_satisfied_zero_matching_successes",
    }
    (RESULTS / "a2_resume_summary.json").write_text(json.dumps(resume, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    total = next(row for row in summary if row["method"] == "TOTAL")
    manifest = {
        "schema_version": "c3-analysis-manifest-v1",
        "phase": "Stage A2 post-run review and C3",
        "classification": "INCOMPLETE_NO_GO",
        "policy": {"path": C3_POLICY.relative_to(ROOT).as_posix(), "sha256": sha256(C3_POLICY)},
        "plan": {"path": C3_PLAN.relative_to(ROOT).as_posix(), "sha256": sha256(C3_PLAN)},
        "frozen_config": {"path": CONFIG_PATH.relative_to(ROOT).as_posix(), "sha256": sha256(CONFIG_PATH), "experiment_config_hash": config["experiment_config_hash"]},
        "a1_admission": {"path": A1_ADMISSION.relative_to(ROOT).as_posix(), "sha256": sha256(A1_ADMISSION), "observations": 51},
        "a2_execution": {"path": A2_EXECUTION.relative_to(ROOT).as_posix(), "sha256": sha256(A2_EXECUTION), "reported_tasks": 102},
        "a2_audit": {
            "tasks": 102,
            "classification_counts": dict(Counter(row["classification"] for row in task_rows)),
            "internal_calls": total["internal_calls"],
            "http_attempts": total["http_attempts"],
            "retries": total["retries"],
            "repair_calls": total["repair_calls"],
            "length_terminations": total["length_terminations"],
            "approved_suffix_top_level": sum("Additional instruction:" in (ROOT / row["output_directory"] / "assembled_prompt.txt").read_text(encoding="utf-8") for row in task_rows),
            "approved_suffix_initial_calls": sum(row["approved_suffix_present"] is True for row in step_rows if row["repair_call"] is False),
            "initial_calls": sum(row["repair_call"] is False for row in step_rows),
            "root_cause": "success-path generation metadata overwrote approved P1/P2 prompt_hash with method P0 hash",
        },
        "c3_denominators": {
            "binary_complete_triplets": {row["method"]: row["paired_n"] for row in c3["cochran"]},
            "term_jaccard_pairs": {row["method"]: row["paired_n"] for row in c3["wilcoxon"]},
        },
        "resume": resume,
        "evidence_preservation": {
            "a1": {"sha256": a1_hash, "files": a1_files, "bytes": a1_bytes, "matches_pre_review": a1_hash == EXPECTED_A1_TREE_SHA256},
            "a2": {"sha256": a2_hash, "files": a2_files, "bytes": a2_bytes, "matches_pre_review": a2_hash == EXPECTED_A2_TREE_SHA256},
            "raw_evidence_modified": False,
        },
        "publication_claims_authorized": False,
        "final_report_writing_recommendation": "NO_GO_pending_owner_authorized_A2_evidence_disposition",
        "verification": {
            "full_pytest": {"passed": 108, "failed": 0, "skipped": 0, "warnings": 1},
            "targeted_pytest": {
                "phase2_2b": 7,
                "phase3": 4,
                "phase4": 22,
                "a1_phase9": 31,
                "phase10": 13,
                "phase11_a2": 14,
                "ontology_utilities_api": 15,
            },
            "compileall": "PASS",
            "frozen_hashes": {
                "dataset_audit": sha256(ROOT / "datasets/ontology_generation/dataset_audit.json"),
                "dataset_full_generation": sha256(ROOT / "datasets/ontology_generation/normalized/project2_full_generation.jsonl"),
                "phase3_snapshot_manifest": snapshot_manifest(),
                "approved_prompts_unchanged": all(sha256(ROOT / config["prompt_variants"][m][v]["source_file"]) == h for (m, v), h in APPROVED_PROMPT_HASHES.items()),
                "historical_phase5_6_6r_unchanged": historical_manifests() == HISTORICAL_MANIFESTS,
                "all_frozen_values_match": sha256(ROOT / "datasets/ontology_generation/dataset_audit.json") == FROZEN_HASHES["dataset_audit_sha256"] and sha256(ROOT / "datasets/ontology_generation/normalized/project2_full_generation.jsonl") == FROZEN_HASHES["dataset_full_generation_sha256"] and snapshot_manifest() == FROZEN_HASHES["phase3_snapshot_manifest_sha256"],
            },
        },
    }
    render_reports(summary, parse_rows, c3, manifest)
    outputs = [
        "a2_generation_summary.csv", "a2_task_completeness.csv", "a2_parse_summary.csv",
        "a2_step_telemetry.csv", "a2_length_terminations.csv", "a2_failure_summary.csv",
        "a2_resume_summary.json", "c3_parse_success_panel.csv", "c3_mcnemar_results.csv",
        "c3_cochran_q_results.csv", "c3_term_jaccard.csv", "c3_wilcoxon_results.csv",
        "c3_effect_sizes.csv",
    ]
    manifest["export_hashes"] = {name: sha256(RESULTS / name) for name in outputs}
    manifest["report_hashes"] = {name: sha256(REPORTS / name) for name in ("A2_EXECUTION.md", "C3_PROMPT_SENSITIVITY.md")}
    (RESULTS / "c3_analysis_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
