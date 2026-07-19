import os
import re
from typing import Any, Dict, List, Tuple

from openai import OpenAI


def _load_prompt_template(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _build_prompt(template: str, cq: str, story: str, ontology_text: str) -> str:
    prompt = template
    replacements = {
        "<<CQ>>": cq.strip(),
        "<<STORY>>": story.strip(),
        "<<OS>>": story.strip(),
        "<<ONTOLOGY>>": ontology_text,
        "<<OWL>>": ontology_text,
        "{CQ}": cq.strip(),
        "{story}": story.strip(),
        "{OS}": story.strip(),
        "{OWL}": ontology_text,
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)
    return prompt


def _extract_label(text: str) -> str:
    match = re.search(r"\*\*\s*(Yes|No)\s*\*\*", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(Yes|No)\b", text, re.IGNORECASE)
    if not match:
        return "no"
    return match.group(1).lower()


def _extract_sparql(text: str) -> str:
    blocks = re.findall(r"```sparql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if not blocks:
        blocks = re.findall(r"```\s*(.*?)```", text, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    match = re.search(r"(SELECT\s+.+)", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _call_openai(prompt: str, model: str, max_tokens: int) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if model.startswith("gpt-5"):
        response = client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=max_tokens,
            instructions="You are an ontology evaluation assistant.",
        )
        return response.output_text or ""
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are an ontology evaluation assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    return completion.choices[0].message.content or ""


def evaluate_ontology_with_llm(
    ontology_text: str,
    competency_questions: List[str],
    story: str,
    prompt_path: str,
    model: str,
    max_tokens: int,
    max_chars: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    template = _load_prompt_template(prompt_path)
    trimmed_ontology = ontology_text[:max_chars] if max_chars > 0 else ontology_text

    results: List[Dict[str, Any]] = []
    for cq in competency_questions:
        prompt = _build_prompt(template, cq, story, trimmed_ontology)
        try:
            raw = _call_openai(prompt, model, max_tokens)
            label = _extract_label(raw)
            sparql = _extract_sparql(raw)
            results.append(
                {
                    "competency_question": cq,
                    "label": label,
                    "sparql": sparql,
                    "raw_response": raw,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "competency_question": cq,
                    "label": "no",
                    "sparql": "",
                    "error": str(exc),
                }
            )

    yes_count = sum(1 for r in results if r.get("label") == "yes")
    total = len(results)
    no_count = total - yes_count
    summary = {
        "yes": yes_count,
        "no": no_count,
        "total": total,
        "yes_ratio": (yes_count / total) if total else 0.0,
    }
    return results, summary
