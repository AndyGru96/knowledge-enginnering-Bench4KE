# Reproducibility

This document separates three reproducibility levels:

1. verification available in a lightweight GitHub clone;
2. deterministic re-analysis available after restoring the immutable evidence archive;
3. model generation, which creates new evidence and requires the exact frozen runtime.

The levels must not be conflated. In particular, the absence of raw A1/A2 response trees from the lightweight repository prevents one-command regeneration of every final table from a fresh clone.

## Validated Python environment

The project test suite was validated with Python 3.13.11 and the direct dependencies in `requirements.txt`. The file uses the tested FastAPI 0.136.3 and Pydantic 2.12.4 combination. It is a curated direct-dependency profile, not a global environment freeze.

From the repository root on Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Verification in a lightweight clone

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall restapi scripts tests
```

`pytest.ini` adds the repository root and `restapi` to the import path. This supports imports through `restapi.*` and `scripts.*` while retaining legacy service imports under `app.*`.

The included provider and prompt-assembly tests use deterministic mocks. These checks do not start Ollama, pull a model, call OpenAI, call `/api/chat`, or contact another real LLM provider.

The lightweight repository also contains:

- frozen normalized datasets and prompt files;
- compact final CSV/JSON results;
- figure and experiment manifests;
- the LaTeX report and compiled PDF;
- source fixtures required by dataset and prompt tests;
- a per-file SHA-256 manifest.

## Deterministic re-analysis without model calls

The following scripts analyze preserved evidence and generate deterministic summaries or figures:

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_stage_a1
.\.venv\Scripts\python.exe -m scripts.analyze_stage_a2_c3
.\.venv\Scripts\python.exe -m scripts.prepare_phase13_final_integration
.\.venv\Scripts\python.exe -m scripts.prepare_phase14_delivery
```

They do not require a live model provider. However, the first three depend on immutable A1/A2 raw evidence and historical sidecar inputs that are intentionally excluded from the lightweight GitHub repository. To reproduce final analysis from raw evidence:

1. obtain the separately preserved evidence archive;
2. verify its published tree and file hashes;
3. restore it under the documented `outputs/`, `results/`, and `reports/` paths without modifying committed files;
4. run the deterministic scripts in the order shown above;
5. compare generated hashes with the committed manifests.

Without the evidence archive, the committed compact results remain inspectable, but full raw-to-report regeneration is not available. This limitation is intentional and must remain explicit.

## Model-generation boundary

`scripts/run_stage_a1.py` and `scripts/run_stage_a2.py` are generation runners. They are not invoked by the verification commands above. Running them creates new model requests and new output artifacts; it is not a deterministic reconstruction of historical A1/A2 evidence.

The reported experiment used:

- Ollama 0.32.0;
- model `qwen3:30b-a3b-instruct-2507-q4_K_M`;
- digest `19e422b0231392335cfc49cfd172de7034bb1aeabb08aa307cce745c60b272fe`;
- temperature 0 and seed 42;
- `num_ctx=32768`, `num_predict=8192`, `keep_alive=30m`;
- non-streaming responses.

A new model execution must use a separately reviewed configuration and output namespace. It must never overwrite or be presented as the original A1/A2 evidence.

## Versioned cache identities

Experiment configurations use `cache_identity_schema_version: 2`; the policy is `config/cache_identity_schema_v2.yaml`. Schema v2:

- uses one canonicalizer in planning and runtime paths;
- includes the schema marker in the cache identity;
- normalizes semantically equivalent numeric values;
- rejects non-finite numbers;
- includes prompt-variant identity;
- validates preserved internal-call evidence before reuse.

The exact byte-level policy is specified in `docs/CACHE_IDENTITY_SCHEMA_V2.md`. Historical schema-v1 keys remain auditable but are not automatic schema-v2 cache hits.

## Evidence-admission history

The following classifications are independent and must remain visible:

- Stage A1 historical schema-v1 identity: **FAIL**;
- A1 immutable evidence: **ADMITTED** through schema-v2 semantic-equivalence audit;
- Stage A2 historical strict schema-v2 identity: **FAIL**;
- Phase 12 strict sidecar admission: **PARTIALLY ADMITTED**;
- Phase 12B repair-aware sidecar admission: **ADMITTED** for downstream analysis;
- B10: **RESOLVED** through repair-aware admission;
- B6: **OPEN / non-blocking**, monitored as an Ontogenia model-output quality risk.

Repair-aware admission does not alter the original envelopes, cache keys, prompts, responses, parse artifacts, or telemetry. It does not convert the historical Stage A2 strict failure into a pass.

## Result interpretation

The experiment evaluates instruction-level suffix sensitivity, not unrestricted semantic prompt rephrasing. The paired parse-success analysis found no statistically significant superiority for P1 or P2. Descriptive term-Jaccard results nevertheless show substantial ontology-content drift from P0 where comparisons are available. Paired P1/P2 drift magnitude is not statistically distinguishable under the frozen policy.

Parseability is a syntactic property and does not establish ontology semantic quality, CQ coverage, logical consistency, or practical usefulness. The experiment is additionally limited to one quantized local model, one seed, 17 dataset items, and one frozen context/output budget.

## Integrity checks

The dataset audit and normalized full-generation file retain these frozen hashes:

- `datasets/ontology_generation/dataset_audit.json`: `f9764b85125f51144a2e476a865a9b9d6a453393f62d33ab5644d5786a3f71d0`;
- `datasets/ontology_generation/normalized/project2_full_generation.jsonl`: `65826ac4dc0a0ddaf5300a401909f9686be9a7f3767e4ca3c0f1336e6bfa9632`;
- dataset manifest: `e06831a155503aa5c2faa8312b7bd78eb6778b124f31dbfb1617bc63c6664caf`.

Raw output directories, caches, virtual environments, temporary test directories, and packaged ZIP files are excluded from version control by `.gitignore`.
