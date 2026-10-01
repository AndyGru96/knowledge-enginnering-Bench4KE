"""Benchmark API and validation of dataset, plan, requests and saved outputs."""

from collections.abc import Iterator
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from restapi.app.config import METHODS, OLLAMA_BASE_URL
from restapi.ontology_adapter import assemble_prompts, run_benchmark, task_plan
from restapi.app.models_ontology import BenchmarkRequest, DatasetItem
from restapi.app.utils.llm_clients import GenerationOptions, OllamaAdapter, build_chat_request
from restapi.app.utils.ontology_artifacts import as_bool, graph_flags, load_task_graph, read_csv, run_file, sha256
from restapi.app.utils.ontology_dataset import validate_items

router = APIRouter()


def get_client() -> Iterator[OllamaAdapter]:
    client = OllamaAdapter(OLLAMA_BASE_URL)
    try:
        yield client
    finally:
        client.session.close()


@router.post("/ontology/run")
def ontology_run(request: BenchmarkRequest, client: OllamaAdapter = Depends(get_client)):
    try:
        return run_benchmark(request, client)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def validate_run(run_dir: Path, allow_partial: bool = False) -> tuple[dict, list[dict]]:
    def read(name):
        return json.loads(run_file(run_dir, name).read_text(encoding="utf-8"))

    manifest = read("manifest.json")
    settings = read("settings.json")
    request = BenchmarkRequest(**settings)
    items = validate_items([DatasetItem(**value) for value in read("dataset.json")])
    if request.max_items and len(items) > request.max_items:
        raise ValueError("Dataset snapshot exceeds max_items")
    methods = METHODS if request.system == "all" else (request.system,)
    expected = task_plan(items, methods, request.prompt_variants)
    plan = read("plan.json")
    if len(plan) != len(expected) or any(any(task.get(k) != value for k, value in wanted.items()) for task, wanted in zip(plan, expected)):
        raise ValueError("Plan does not match dataset/settings task combinations")
    for name in ("settings.json", "dataset.json", "plan.json"):
        if sha256(run_file(run_dir, name).read_bytes()) != manifest["input_sha256"][name]:
            raise ValueError(f"Run input changed: {name}")
    if manifest["methods"] != list(methods) or manifest["prompt_variants"] != request.prompt_variants or manifest["dataset_items"] != len(items) or manifest["tasks"] != len(plan):
        raise ValueError("Manifest does not match the dataset/settings/plan")
    rows = read_csv(run_dir / "task_index.csv")
    if len(rows) != len(plan):
        raise ValueError("Task index does not match the plan")
    by_id = {item.dataset_id: item for item in items}
    options = GenerationOptions(model=request.model, temperature=request.temperature, seed=request.seed,
                                num_ctx=request.num_ctx, max_output_tokens=request.max_output_tokens,
                                timeout_seconds=request.timeout_seconds, keep_alive=request.keep_alive)
    for row, planned in zip(rows, plan):
        if any(row.get(k) != str(value) for k, value in planned.items()):
            raise ValueError("Task index contains an unexpected dataset/method/variant or plan field")
        if row["status"] not in {"pending", "running", "completed"}:
            raise ValueError("Unknown task status")
        if row["status"] != "completed":
            if as_bool(row["result_available"]) or as_bool(row["parse_success"]):
                raise ValueError("Unfinished task claims an available result")
            continue
        task_dir = f"tasks/{row['task_id']}"
        result = read(f"{task_dir}/result.json")
        if any(row.get(k) != ("" if value is None else str(value)) for k, value in result.items() if k in row):
            raise ValueError("Task result disagrees with its index")
        template = run_file(run_dir, f"{task_dir}/template.txt").read_bytes().decode("utf-8")
        if sha256(template.encode("utf-8")) != planned["prompt_template_sha256"]:
            raise ValueError("Task template does not match its recorded hash")
        prompts = assemble_prompts(row["method"], by_id[row["dataset_id"]], template)
        calls = result["calls"]
        accepted = []
        history = []
        for number, call in enumerate(calls, 1):
            call_dir = f"{task_dir}/calls/call_{number:02d}"
            prompt = run_file(run_dir, f"{call_dir}/prompt.txt").read_bytes().decode("utf-8")
            if number > len(prompts) or prompt != prompts[number - 1] or call["number"] != number:
                raise ValueError("Call prompt does not match its task and dataset")
            messages = history + [{"role": "user", "content": prompt}]
            if read(f"{call_dir}/request.json") != build_chat_request(messages, options):
                raise ValueError("Call request does not match run settings and prompt history")
            if not call["accepted"]:
                if number != len(calls) or as_bool(row["result_available"]):
                    raise ValueError("Failed call is inconsistent with the task result")
                continue
            data = read(f"{call_dir}/response.json")
            output = run_file(run_dir, f"{call_dir}/output.txt").read_bytes().decode("utf-8")
            if data.get("done") is not True or data.get("done_reason") == "length" or data.get("message", {}).get("content") != output:
                raise ValueError("Accepted output disagrees with its response or completion state")
            if row["method"] == "neon-gpt":
                history = messages + [{"role": "assistant", "content": output}]
            if number in planned["ontology_call_numbers"]:
                accepted.append({"path": f"{call_dir}/output.txt", "sha256": sha256(output.encode("utf-8"))})
        if int(row["successful_calls"]) != sum(call["accepted"] for call in calls):
            raise ValueError("Successful call count is inconsistent")
        if as_bool(row["result_available"]) and (len(calls) != planned["planned_calls"] or not all(call["accepted"] for call in calls)):
            raise ValueError("Incomplete calls claim a complete result")
        if json.loads(row["documents_json"]) != accepted:
            raise ValueError("Ontology document list or content hash changed")
        transcript = run_file(run_dir, row["transcript_path"]).read_bytes()
        expected_transcript = "\n\n".join(run_file(run_dir, doc["path"]).read_bytes().decode("utf-8") for doc in accepted).encode("utf-8")
        if row["transcript_path"] != f"{task_dir}/ontology_transcript.txt" or transcript != expected_transcript or sha256(transcript) != row["transcript_sha256"]:
            raise ValueError("Task transcript changed or belongs to a different task")
        if as_bool(row["result_available"]):
            graph, error = load_task_graph(row, run_dir)
            if as_bool(row["parse_success"]) != (graph is not None):
                raise ValueError("Recorded parse outcome disagrees with the original documents")
            if graph is not None:
                flags = graph_flags(graph)
                if int(row["triples"]) != len(graph) or any(as_bool(row[k]) != value for k, value in flags.items()):
                    raise ValueError("Recorded graph size or empty/entity flags are inconsistent")
    if not allow_partial and (manifest["status"] != "completed" or any(row["status"] != "completed" for row in rows)):
        raise ValueError("Run is incomplete; use --allow-partial to analyze completed tasks with pending tasks explicitly missing")
    return manifest, rows


