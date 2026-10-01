"""Load one explicit JSONL dataset, rejecting ambiguous or repeated records."""

import json
from pathlib import Path

from restapi.app.models_ontology import DatasetItem


def validate_items(items: list[DatasetItem]) -> list[DatasetItem]:
    seen = set()
    for item in items:
        if item.dataset_id in seen:
            raise ValueError(f"Duplicate dataset_id: {item.dataset_id}")
        seen.add(item.dataset_id)
    if not items:
        raise ValueError("Dataset contains no items")
    return items


def load_ontology_items(path: Path) -> list[DatasetItem]:
    if not path.is_file() or path.suffix.lower() != ".jsonl":
        raise ValueError(f"Dataset must be one existing JSONL file: {path}")
    items = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            items.append(DatasetItem.model_validate(json.loads(line)))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid dataset row {number}: {exc}") from exc
    return validate_items(items)
