# Bench4KE: Local Ontology Generation Evaluation

Knowledge Engineering course project by Gu Mingxuan (`mingxuan.gu@studio.unibo.it`) and Chayan Talukder (`chayan.talukder@studio.unibo.it`).

The project extends Bench4KE with local ontology generation through Ollama, documentation-completeness measurements, and prompt-sensitivity analysis.

The experiments use 17 scenarios and 74 competency questions. The compared methods are the Ontogenia-labelled Memoryless CQ-by-CQ baseline, Domain-OntoGen, and NeOn-GPT. The local model is `qwen3:30b-a3b-instruct-2507-q4_K_M`.

The method called `ontogenia` in the experiment is the Memoryless CQ-by-CQ baseline: each competency question is processed independently, previous model outputs are not passed to later calls, and the resulting fragments are combined at task level. The separate metacognitive `ontogenia-mp` implementation was not used.

## Results

The table reports final pipeline parse success after the recorded normalization and repair steps.

| Method | P0 | P1 | P2 | Total |
|---|---:|---:|---:|---:|
| Ontogenia-labelled Memoryless CQ-by-CQ | 4/17 | 5/17 | 5/17 | 14/51 |
| Domain-OntoGen | 16/17 | 17/17 | 17/17 | 50/51 |
| NeOn-GPT | 11/17 | 12/17 | 14/17 | 37/51 |

Overall, 101 of 153 final ontologies were parseable. Documentation metrics are available for those 101 outputs. The paired tests did not detect a significant parse-success difference among P0, P1, and P2, while term overlap showed that ontology content could still change substantially.

The full discussion is in [report/FINAL_REPORT.pdf](report/FINAL_REPORT.pdf).

## Repository structure

```text
config/                         Experiment and analysis settings
datasets/ontology_generation/   Dataset, prompts, mappings, and gold modules
docs/                           Method, dataset, and reproducibility notes
evidence/                       One complete A2 P1 request example
external_resources/             Source material used by the project
report/                         LaTeX source, figures, bibliography, and PDF
restapi/                        FastAPI service and Ollama integration
results/                        Tables and statistical outputs
scripts/                        Dataset, prompt-validation, and analysis scripts
tests/                          Automated tests
```

## Installation

Python 3.13 was used for the submitted version.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Tests

```powershell
python -m pytest -q
python -m compileall restapi scripts tests
```

The current repository test suite contains 47 passing tests. Provider tests use local recording objects and do not contact an Ollama server.

## Running the services

Start the API:

```powershell
cd restapi
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the ontology-generation adapter in another terminal:

```powershell
cd restapi
python ontology_adapter.py
```

The adapter uses Ollama at `http://localhost:11434` by default and listens on port `8020`.

## Report

```powershell
powershell -ExecutionPolicy Bypass -File report/build_report.ps1
```

Report files:

- [FINAL_REPORT.pdf](report/FINAL_REPORT.pdf)
- [main.tex](report/main.tex)
- [references.bib](report/references.bib)

## Data and evidence

The executable dataset contains 17 scenarios and 74 source-ordered competency questions. The source workbook contains 112 nonempty CQ rows; 38 rows without a reliable story or scenario link are listed in the reconciliation table but are not used for generation.

Important files:

- [normalized dataset](datasets/ontology_generation/normalized/project2_full_generation.jsonl)
- [dataset preparation](docs/DATASET_PREPARATION.md)
- [method mapping](docs/METHOD_MAPPING.md)
- [A2 request example](evidence/README.md)
- [final C2/C3 summary](results/final_c2_c3_summary.csv)
- [C2 task-level results](results/c2_documentation_completeness.csv)
- [C2 method and variant summary](results/c2_documentation_summary.csv)
- [C3 parse-success panel](results/c3_parse_success_panel.csv)
- [Cochran Q results](results/c3_cochran_q_results.csv)
- [McNemar results](results/c3_mcnemar_results.csv)
- [Wilcoxon results](results/c3_wilcoxon_results.csv)
- [effect sizes](results/c3_effect_sizes.csv)

The C2 implementation is in
[`scripts/analyze_documentation_completeness.py`](scripts/analyze_documentation_completeness.py).
The complete C3 implementation is in
[`scripts/analyze_prompt_sensitivity.py`](scripts/analyze_prompt_sensitivity.py).

One A2 writer error stored the P0 prompt identifier in 94 P1/P2 task metadata files. The saved prompts and request files contain the intended P1/P2 instructions. The original task files were not edited; a complete P1 example and the task-level verification table are included in this repository.

## Limitations

- The evaluation uses one quantized local model, one seed, and one generation configuration.
- Some paired comparisons have small sample sizes.
- Parse success does not establish conceptual correctness, CQ coverage, logical consistency, or practical usefulness.
- Documentation measurements describe the parseable outputs and do not assess the semantic quality of their text.
- Normalization and syntax repair affect final parse success.

## References

1. Ciancarini, P., et al. “Bench4KE: Benchmarking Automated Competency Question Generation.” *The Semantic Web — ESWC 2026*, 2026. [DOI](https://doi.org/10.1007/978-3-032-25159-6_12)
2. Lippolis, A. S., et al. “Ontogenia: Ontology Generation with Metacognitive Prompting in Large Language Models.” *The Semantic Web: ESWC 2024 Satellite Events*, published 2025. [DOI](https://doi.org/10.1007/978-3-031-78952-6_38)
3. Lippolis, A. S., et al. “Ontology Generation Using Large Language Models.” *The Semantic Web — ESWC 2025*, 2025. [DOI](https://doi.org/10.1007/978-3-031-94575-5_18)
4. Lippolis, A. S., et al. “Assessing the Capability of Large Language Models for Domain-Specific Ontology Generation.” *ESWC 2025 Workshops and Tutorials*, 2025. [Paper](https://ceur-ws.org/Vol-3977/elmke-2.pdf)
5. Fathallah, N., et al. “NeOn-GPT: A Large Language Model-Powered Pipeline for Ontology Learning.” 2024. [Zenodo](https://doi.org/10.5281/zenodo.11221931)
6. Garijo, D. “WIDOCO: A Wizard for Documenting Ontologies.” *ISWC 2017*, 2017. [DOI](https://doi.org/10.1007/978-3-319-68204-4_9)
7. Zhu, K., et al. “PromptBench: Towards Evaluating the Robustness of Large Language Models on Adversarial Prompts.” 2023. [arXiv](https://arxiv.org/abs/2306.04528)
8. FOSSr Project. [Bench4KE / Ontogenia-CINI repository](https://github.com/fossr-project/ontogenia-cini).

The complete BibTeX records are in [report/references.bib](report/references.bib). The implementation also uses [Ollama](https://ollama.com/), [RDFLib](https://rdflib.readthedocs.io/), [FastAPI](https://fastapi.tiangolo.com/), [RDF 1.1 Turtle](https://www.w3.org/TR/turtle/), and [OWL 2](https://www.w3.org/TR/owl2-overview/).

## License

Upstream Apache-2.0 material is distributed under the terms in [LICENSE](LICENSE). External resources remain subject to their original terms.
