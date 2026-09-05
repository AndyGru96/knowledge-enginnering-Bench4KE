from __future__ import annotations

import pytest

from scripts.analyze_prompt_sensitivity import (
    analyze,
    build_term_jaccard_rows,
    jaccard,
    normalize_iri,
)


def test_iri_normalization_and_jaccard() -> None:
    assert normalize_iri("HTTP://Example.org:80/A%62") == "http://example.org/Ab"
    assert jaccard(set(), set()) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_parse_success_statistics_reproduce_saved_results() -> None:
    result = analyze()
    cochran = {row["method"]: row for row in result["cochran"]}
    assert cochran["ontogenia"]["paired_n"] == 17
    assert cochran["ontogenia"]["p_value"] == pytest.approx(0.9048374180359595)
    assert cochran["domain-ontogen"]["p_value"] == pytest.approx(0.36787944117144245)
    assert cochran["neon-gpt"]["p_value"] == pytest.approx(0.5292133415000504)
    assert len(result["mcnemar"]) == 9
    assert all(row["p_value_holm"] == 1.0 for row in result["mcnemar"])


def test_term_sensitivity_statistics_reproduce_saved_results() -> None:
    result = analyze()
    wilcoxon = {row["method"]: row for row in result["wilcoxon"]}
    assert wilcoxon["ontogenia"]["paired_n"] == 0
    assert wilcoxon["domain-ontogen"]["paired_n"] == 16
    assert wilcoxon["domain-ontogen"]["p_value"] == pytest.approx(0.33795442469135184)
    assert wilcoxon["domain-ontogen"]["matched_rank_biserial"] == pytest.approx(0.2781954887218045)
    assert wilcoxon["neon-gpt"]["paired_n"] == 6
    assert wilcoxon["neon-gpt"]["p_value"] == pytest.approx(0.1956677425436415)
    assert wilcoxon["neon-gpt"]["matched_rank_biserial"] == pytest.approx(-0.6666666666666666)
    assert len(result["effects"]) == 12


def test_term_table_can_be_built_from_ontology_index(tmp_path) -> None:
    variants = {"P0": ("A", "B"), "P1": ("A", "B"), "P2": ("A", "C")}
    for variant, classes in variants.items():
        declarations = "\n".join(f":{name} a owl:Class ." for name in classes)
        (tmp_path / f"{variant}.ttl").write_text(
            "@prefix : <https://example.org/test#> .\n"
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
            + declarations,
            encoding="utf-8",
        )
    index = [
        {
            "method": "ontogenia",
            "dataset_id": "dataset-1",
            "prompt_variant": variant,
            "result_available": "true",
            "final_parse_success": "true",
            "ontology_path": f"{variant}.ttl",
        }
        for variant in ("P0", "P1", "P2")
    ]
    rows = build_term_jaccard_rows(index, tmp_path)
    assert len(rows) == 1
    assert rows[0]["J_P0_P1"] == 1.0
    assert rows[0]["J_P0_P2"] == pytest.approx(1 / 3)
    assert rows[0]["included_in_wilcoxon"] is True
