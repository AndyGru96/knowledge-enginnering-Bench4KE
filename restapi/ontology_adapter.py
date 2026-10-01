import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

import yaml

from restapi.app.config import DATASET, METHODS, OLLAMA_BASE_URL, OUTPUTS, PROCEDURES, ROOT, VARIANTS, project_path
from restapi.app.models_ontology import BenchmarkRequest, DatasetItem
from restapi.app.utils.llm_clients import GenerationOptions, OllamaAdapter, ProviderError, build_chat_request
from restapi.app.utils.ontology_artifacts import graph_flags, parse_documents, scenario_base, sha256, write_bytes, write_csv, write_json, write_text
from restapi.app.utils.ontology_dataset import load_ontology_items, validate_items

def task_plan(items: list[DatasetItem], methods, variants) -> list[dict]:
    return [
        {"task_id": f"task_{number:04d}", "dataset_id": item.dataset_id,
         "method": method, "prompt_variant": variant,
         "procedure": PROCEDURES[method],
         "parse_base": scenario_base(item.dataset_id),
         "planned_calls": 10 if method == "neon-gpt" else len(item.competency_questions),
         "ontology_call_numbers": list(range(5, 11)) if method == "neon-gpt" else list(range(1, len(item.competency_questions) + 1))}
        for number, (method, variant, item) in enumerate(
            ((m, v, i) for m in methods for v in variants for i in items), 1
        )
    ]


OUTPUT_PROTOCOL = (
    "Return exactly one complete Turtle document without Markdown fences. "
    "Write any requested explanation or answerability discussion only as Turtle comments beginning with #. "
    "This serialization instruction takes precedence over conflicting prose-output instructions."
)


def load_template(method: str, variant: str) -> tuple[str, str]:
    if method not in METHODS or variant not in VARIANTS:
        raise ValueError("Unsupported method or prompt variant")
    filenames = {"P0": "P0_original.txt", "P1": "P1_candidate.txt", "P2": "P2_candidate.txt"}
    path = ROOT / "datasets/ontology_generation/prompts" / method / filenames[variant]
    template = path.read_bytes().decode("utf-8")
    return template, hashlib.sha256(template.encode("utf-8")).hexdigest()


def assemble_prompts(method: str, item: DatasetItem, template: str) -> list[str]:
    if method == "ontogenia":
        return [
            template.replace("{story}", item.scenario).replace("{CQ}", cq).replace("{rdf}", "")
            for cq in item.competency_questions
        ]
    if method == "domain-ontogen":
        return [
            template.replace("{OS}", item.scenario).replace("{CQ}", cq)
            for cq in item.competency_questions
        ]
    if method == "neon-gpt":
        base, instructions = template.split("\n\nOutput protocol:\n\n", 1)
        steps = re.split(r"(?m)^Prompt \d+:\s*\n", base)[1:]
        if len(steps) != 10:
            raise ValueError("NeOn procedure must contain ten ordered prompts")
        questions = "\n".join(f"- {cq}" for cq in item.competency_questions)
        context = f"\n\nScenario:\n{item.scenario}\n\nGiven competency questions:\n{questions}"
        return [step.strip() + (context if n == 1 else "") +
                ("\n\nOutput protocol:\n\n" + instructions if n >= 5 else "")
                for n, step in enumerate(steps, 1)]
    raise ValueError(f"Unsupported method: {method}")


def run_benchmark(request: BenchmarkRequest, client: OllamaAdapter, output_root: Path = OUTPUTS) -> dict:
    items = validate_items(request.items) if request.items is not None else load_ontology_items(
        project_path(request.dataset_path) if request.dataset_path else DATASET
    )
    if request.max_items:
        items = items[:request.max_items]
    methods = METHODS if request.system == "all" else (request.system,)
    templates = {(m, v): load_template(m, v) for m in methods for v in request.prompt_variants}
    options = GenerationOptions(
        model=request.model, temperature=request.temperature, seed=request.seed,
        num_ctx=request.num_ctx, max_output_tokens=request.max_output_tokens,
        timeout_seconds=request.timeout_seconds, keep_alive=request.keep_alive,
    )
    run_dir = output_root / uuid.uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False)
    settings = request.model_dump(mode="json", exclude={"items"})
    dataset = [item.model_dump(mode="json") for item in items]
    plan = task_plan(items, methods, request.prompt_variants)
    for task in plan:
        task["prompt_template_sha256"] = templates[(task["method"], task["prompt_variant"])][1]
    write_json(run_dir / "settings.json", settings)
    write_json(run_dir / "dataset.json", dataset)
    write_json(run_dir / "plan.json", plan)
    results = [{
        **task, "status": "pending", "result_available": False, "parse_success": False,
        "syntax_endpoint": "whole_response_turtle", "ontology_code_syntax_success": None,
        "transcript_path": f"tasks/{task['task_id']}/ontology_transcript.txt", "transcript_sha256": "",
        "documents_json": "[]", "successful_calls": 0, "triples": None,
        "empty_ontology": None, "has_evaluable_entities": None,
        "parse_error": "Task not completed", "generation_error": "",
    } for task in plan]
    manifest = {
        "run_id": run_dir.name, "created_at": datetime.now(timezone.utc).isoformat(),
        "methods": list(methods), "prompt_variants": request.prompt_variants,
        "dataset_items": len(items), "tasks": len(plan), "completed_tasks": 0,
        "status": "planned", "syntax_endpoint": "whole_response_turtle",
        "output_handling": "Responses parsed independently; blank nodes scoped to each response; joined text is a record only",
        "input_sha256": {name: sha256((run_dir / name).read_bytes()) for name in ("settings.json", "dataset.json", "plan.json")},
    }

    def checkpoint():
        manifest["completed_tasks"] = sum(row["status"] == "completed" for row in results)
        write_csv(run_dir / "task_index.csv", results)
        write_json(run_dir / "manifest.json", manifest)

    checkpoint()
    try:
        info = client.preflight(request.model, timeout_seconds=min(30, request.timeout_seconds))
        write_json(run_dir / "preflight.json", info)
        if not info["model_available"]:
            raise ProviderError("model_not_found", info["missing_model_error"]["message"])
        manifest["status"] = "running"
        checkpoint()
        item_by_id = {item.dataset_id: item for item in items}
        for row in results:
            task_dir = run_dir / "tasks" / row["task_id"]
            template, _ = templates[(row["method"], row["prompt_variant"])]
            write_text(task_dir / "template.txt", template)
            prompts = assemble_prompts(row["method"], item_by_id[row["dataset_id"]], template)
            outputs, documents, calls, error = [], [], [], None
            row["status"] = "running"
            checkpoint()
            started = time.perf_counter()
            history = []
            for number, prompt in enumerate(prompts, start=1):
                call_dir = task_dir / "calls" / f"call_{number:02d}"
                messages = history + [{"role": "user", "content": prompt}]
                write_text(call_dir / "prompt.txt", prompt)
                write_json(call_dir / "request.json", build_chat_request(messages, options))

                def retain_response(event):
                    attempt_dir = call_dir / "http" / f"attempt_{event['attempt']:02d}"
                    write_bytes(attempt_dir / "body.bin", event["body"])
                    write_json(attempt_dir / "metadata.json", {"attempt": event["attempt"], "status_code": event["status_code"]})
                    if event["json"] is not None:
                        write_json(attempt_dir / "response.json", event["json"])
                        write_json(call_dir / "response.json", event["json"])
                        data = event["json"]
                        message = data.get("message", {}) if isinstance(data, dict) else {}
                        content = message.get("content") if isinstance(message, dict) else None
                        if isinstance(content, str):
                            write_text(call_dir / "output.txt", content)

                try:
                    response = client.chat_completion(messages, options, on_response=retain_response)
                except ProviderError as exc:
                    error = exc.as_dict()
                    write_json(call_dir / "error.json", error)
                    calls.append({"number": number, "accepted": False, "error": error})
                    break
                if row["method"] == "neon-gpt":
                    history = messages + [{"role": "assistant", "content": response.content}]
                output_path = call_dir / "output.txt"
                write_text(output_path, response.content)
                write_json(call_dir / "response.json", response.raw_response)
                if number in row["ontology_call_numbers"]:
                    outputs.append(response.content)
                    documents.append({"path": output_path.relative_to(run_dir).as_posix(),
                                      "sha256": sha256(output_path.read_bytes())})
                calls.append({"number": number, "accepted": True,
                              "prompt_sha256": sha256(prompt.encode("utf-8")),
                              "telemetry": response.telemetry, "attempts": response.attempts})
                row["successful_calls"] = len(calls)
                row["documents_json"] = json.dumps(documents, sort_keys=True)
                write_json(task_dir / "result.json", {**row, "calls": calls})
                checkpoint()
            # The joined text is a transcript; it is never parsed as the merged ontology.
            content = "\n\n".join(outputs)
            ontology = task_dir / "ontology_transcript.txt"
            write_text(ontology, content)
            graph, parse_error = parse_documents(outputs, row["parse_base"]) if error is None else (None, "Generation did not complete")
            row.update({
                "status": "completed", "result_available": error is None,
                "parse_success": graph is not None,
                "ontology_code_syntax_success": True if graph is not None else None,
                "transcript_sha256": sha256(ontology.read_bytes()),
                "documents_json": json.dumps(documents, sort_keys=True),
                "successful_calls": sum(call["accepted"] for call in calls), "triples": len(graph) if graph is not None else None,
                "parse_error": parse_error, "generation_error": json.dumps(error, sort_keys=True) if error else "",
                **graph_flags(graph),
            })
            write_json(task_dir / "result.json", {**row, "calls": calls, "error": error,
                                                "wall_seconds": time.perf_counter() - started})
            checkpoint()
        manifest["status"] = "completed"
        checkpoint()
    except BaseException as exc:
        manifest["status"] = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
        manifest["error"] = exc.as_dict() if isinstance(exc, ProviderError) else {"type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, ProviderError) and not (run_dir / "preflight.json").exists():
            write_json(run_dir / "preflight.json", {"error": exc.as_dict()})
        checkpoint()
        raise
    return {"run_id": run_dir.name, "run_dir": str(run_dir), "tasks": len(results), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/course_methods.yaml")
    parser.add_argument("--output-dir", type=Path, default=OUTPUTS)
    args = parser.parse_args()
    settings = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    request = BenchmarkRequest.model_validate(settings["request"])
    client = OllamaAdapter(OLLAMA_BASE_URL)
    try:
        result = run_benchmark(request, client, project_path(args.output_dir))
    finally:
        client.session.close()
    print(json.dumps({key: value for key, value in result.items() if key != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
