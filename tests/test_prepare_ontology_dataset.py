import shutil

import pytest

from restapi.app.config import DATASET, ROOT, project_path
from restapi.app.utils.ontology_dataset import load_ontology_items, validate_items
from scripts import prepare_ontology_dataset as preparation
from scripts.prepare_ontology_dataset import prepare_dataset
from test_provider_artifacts import item


def test_default_dataset_structure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    items = load_ontology_items(DATASET)
    assert len(items) == len({i.dataset_id for i in items}) == 17
    assert sum(len(i.competency_questions) for i in items) == 74
    assert project_path("datasets/ontology_generation/normalized/project2_full_generation.jsonl") == DATASET
    assert len(list(DATASET.parent.glob("*.jsonl"))) == 1


def test_invalid_dataset_inputs(item, tmp_path):
    with pytest.raises(ValueError, match="Duplicate"):
        validate_items([item, item])
    with pytest.raises(ValueError, match="one existing JSONL"):
        load_ontology_items(tmp_path)


def test_dataset_rebuild_from_bundled_sources(tmp_path):
    rebuilt = prepare_dataset(tmp_path)
    assert rebuilt["scenarios"] == 17
    assert rebuilt["cqs"] == 74
    assert rebuilt["gold_modules"] == 36
    assert rebuilt["excluded_rows"]["missing_story_id"] == 38
    assert (tmp_path / "normalized/project2_full_generation.jsonl").read_bytes() == DATASET.read_bytes()
    assert len(list((tmp_path / "normalized").glob("*.jsonl"))) == 1
    assert (tmp_path / "prompts/neon-gpt/P0_original.txt").is_file()
    assert (tmp_path / "prompts/neon-gpt/P1_candidate.txt").is_file()


def test_dataset_rebuild_is_portable(tmp_path, monkeypatch):
    relocated = tmp_path / "another location"
    shutil.copytree(ROOT / "external_resources", relocated / "external_resources")
    shutil.copytree(ROOT / "datasets", relocated / "datasets")
    shutil.copytree(ROOT / "config", relocated / "config")
    monkeypatch.setattr(preparation, "ROOT", relocated)
    preparation.prepare_dataset(relocated / "datasets/ontology_generation")
    rebuilt = relocated / "datasets/ontology_generation/normalized/project2_full_generation.jsonl"
    assert rebuilt.read_bytes() == DATASET.read_bytes()
    for name in ("normalized/project2_full_generation.jsonl", "gold_mapping.csv", "source_row_reconciliation.csv", "dataset_audit.json"):
        text = (relocated / "datasets/ontology_generation" / name).read_text(encoding="utf-8")
        assert str(ROOT) not in text and str(relocated) not in text

