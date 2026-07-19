# Bench4KE: Local LLM Ontology-Generation Evaluation

Knowledge Engineering course project extending Bench4KE with local Ollama execution, documentation-completeness metrics, and prompt-sensitivity analysis.

**Authors:** Gu Mingxuan (`mingxuan.gu@studio.unibo.it`) and Chayan Talukder (`chayan.talukder@studio.unibo.it`)

## Project scope

The repository implements and evaluates:

- **local Ollama support**, including frozen model identity, generation controls, telemetry, parsing, caching, and resumable execution;
- **C2 documentation completeness**, measuring labels, comments or definitions, ontology metadata, documentation length, and nontrivial documentation;
- **C3 prompt sensitivity**, comparing the approved P0, P1, and P2 instruction variants through paired parse-success tests and ontology-term Jaccard similarity.

The experiment covers all 17 frozen dataset items with the three required methods: **Ontogenia**, **Domain-OntoGen**, and **NeOn-GPT**. Reported generation used the local model `qwen3:30b-a3b-instruct-2507-q4_K_M` through Ollama; no paid API was used.

## Main results

The admitted analysis contains 153 tasks: 51 P0 tasks and 102 P1/P2 tasks.

| Method | P0 parse | P1 parse | P2 parse | Total |
|---|---:|---:|---:|---:|
| Ontogenia | 4/17 | 5/17 | 5/17 | 14/51 |
| Domain-OntoGen | 16/17 | 17/17 | 17/17 | 50/51 |
| NeOn-GPT | 11/17 | 12/17 | 14/17 | 37/51 |

Overall, 101/153 final ontologies were parseable. C2 metrics are reported only for those 101 outputs. Under the frozen C3 policy, no method showed a statistically significant P0/P1/P2 difference in final parse success. This does not establish prompt invariance: paired parseable outputs still show descriptive term drift.

See the [final report](report/FINAL_REPORT.pdf) for the complete methodology, results, statistical interpretation, and limitations.

## Repository contents

```text
config/                         Frozen experiment and analysis policies
datasets/ontology_generation/   Normalized dataset, prompts, audits, and gold modules
docs/                           Technical and reproducibility documentation
external_resources/             Minimal authoritative source fixtures
report/                         LaTeX source, bibliography, figures, and final PDF
restapi/                        FastAPI service, Ollama adapter, metrics, and artifact logic
results/                        Compact final results and manifests
scripts/                        Dataset, execution, and analysis tools
tests/                          Automated tests
```

Large immutable A1/A2 raw-response trees are not included in this lightweight repository. The committed package contains the code, frozen inputs, compact results, hashes, and report. Restoring the separately preserved evidence archive is required to rerun every final analysis from raw model responses.

## Installation

Python 3.13 is the validated development profile.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` contains the validated direct dependencies for the API, analysis scripts, dataset preparation, and tests.

## Tests

```powershell
python -m pytest -q
python -m compileall restapi scripts tests
```

The submitted suite has been validated with all tests passing. Provider tests use deterministic mocks and do not contact Ollama, OpenAI, or another model service.

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

The adapter defaults to port `8020`. Ollama is needed only for a newly authorized generation run; inspecting the repository and running the tests requires no model call.

## Rebuilding the report

```powershell
powershell -ExecutionPolicy Bypass -File report/build_report.ps1
```

The script supports Tectonic, `latexmk`, or `pdflatex` with BibTeX. Report files are:

- [FINAL_REPORT.pdf](report/FINAL_REPORT.pdf)
- [main.tex](report/main.tex)
- [references.bib](report/references.bib)

## Data, results, and reproducibility

The normalized execution dataset contains 17 scenarios and 74 source-ordered competency questions. Dataset construction preserves explicit source relationships and does not use fuzzy story matching.

Key entry points:

- [normalized dataset](datasets/ontology_generation/normalized/project2_full_generation.jsonl)
- [dataset preparation](docs/DATASET_PREPARATION.md)
- [method mapping](docs/METHOD_MAPPING.md)
- [output contract](docs/OUTPUT_CONTRACT.md)
- [reproducibility guide](docs/REPRODUCIBILITY.md)
- [final experiment manifest](results/final_experiment_manifest.json)
- [final C2/C3 summary](results/final_c2_c3_summary.csv)
- [C3 statistical policy](config/c3_analysis_policy.yaml)

Historical cache failures and repair-aware evidence admission are documented without rewriting the original records.

## Main dependencies

Exact versions are listed in `requirements.txt`.

| Area | Libraries and tools |
|---|---|
| API and HTTP | FastAPI, Uvicorn, Pydantic, HTTPX, Requests, python-dotenv |
| Ontology processing | RDFLib |
| Data and analysis | NumPy, SciPy, PyYAML, openpyxl |
| Visualization | Matplotlib |
| Testing | pytest |
| Optional provider compatibility | OpenAI Python SDK; not used in the reported experiment |
| Local model execution | Ollama |
| Report | LaTeX/BibTeX, Tectonic or another supported LaTeX engine |

Authoritative prompt and dataset fixtures are retained under `external_resources/`; frozen copies and source hashes are under `datasets/ontology_generation/`.

## Limitations

- The evaluation uses one quantized local model, one seed, and one frozen generation configuration.
- The dataset has 17 scenarios, limiting some paired statistical comparisons.
- Parseability does not prove conceptual correctness, CQ coverage, logical consistency, or practical usefulness.
- C2 measures documentation presence and coverage, not semantic documentation quality.
- Public source ontologies may introduce contamination risk.
- Full raw-response regeneration requires the separately preserved evidence archive.

## References

1. Ciancarini, P., et al. “Bench4KE: Benchmarking Automated Competency Question Generation.” *The Semantic Web — ESWC 2026*, 2026. [DOI](https://doi.org/10.1007/978-3-032-25159-6_12)
2. Lippolis, A. S., et al. “Ontogenia: Ontology Generation with Metacognitive Prompting in Large Language Models.” *The Semantic Web: ESWC 2024 Satellite Events*, published 2025. [DOI](https://doi.org/10.1007/978-3-031-78952-6_38)
3. Lippolis, A. S., et al. “Ontology Generation Using Large Language Models.” *The Semantic Web — ESWC 2025*, 2025. [DOI](https://doi.org/10.1007/978-3-031-94575-5_18)
4. Lippolis, A. S., et al. “Assessing the Capability of Large Language Models for Domain-Specific Ontology Generation.” *ESWC 2025 Workshops and Tutorials*, 2025. [Paper](https://ceur-ws.org/Vol-3977/elmke-2.pdf)
5. Fathallah, N., et al. “NeOn-GPT: A Large Language Model-Powered Pipeline for Ontology Learning.” 2024. [Zenodo](https://doi.org/10.5281/zenodo.11221931)
6. Garijo, D. “WIDOCO: A Wizard for Documenting Ontologies.” *ISWC 2017*, 2017. [DOI](https://doi.org/10.1007/978-3-319-68204-4_9)
7. Zhu, K., et al. “PromptBench: Towards Evaluating the Robustness of Large Language Models on Adversarial Prompts.” 2023. [arXiv](https://arxiv.org/abs/2306.04528)
8. FOSSr Project. [Bench4KE / Ontogenia-CINI repository](https://github.com/fossr-project/ontogenia-cini).

The complete BibTeX records are in [report/references.bib](report/references.bib). Core implementation resources also include [Ollama](https://ollama.com/), [RDFLib](https://rdflib.readthedocs.io/), [FastAPI](https://fastapi.tiangolo.com/), [RDF 1.1 Turtle](https://www.w3.org/TR/turtle/), and [OWL 2](https://www.w3.org/TR/owl2-overview/).

## License

This repository retains the upstream [Apache License 2.0](LICENSE). External resources remain subject to their original licenses and attribution requirements.
