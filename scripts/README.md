# Script Index

Scripts are kept in their original locations because configuration files, tests, manifests, and evidence records refer to these paths. Generation runners and deterministic analysis scripts have different safety boundaries; read the descriptions before running them.

## Primary reproducibility scripts

| Script | Purpose | Provider calls |
|---|---|---|
| `run_stage_a1.py` | Executes the frozen 17-item x 3-method P0 baseline and writes Stage A1 artifacts. It is a generation runner, not a report-reproduction command. | Yes, unless invoked through a non-generating planning path supported by the script |
| `run_stage_a2.py` | Plans or executes the frozen P1/P2 task panel. `--dry-run` performs planning only; execution/resume/retry modes may contact Ollama. | `--dry-run`: no; other selected-task modes: yes |
| `analyze_stage_a2_c3.py` | Reads preserved A2 artifacts, integrates the admitted A1 baseline, and writes C3 tables/manifests. | No |
| `prepare_phase13_final_integration.py` | Produces deterministic final C2/C3 integration summaries from admitted immutable evidence. | No |
| `prepare_phase14_delivery.py` | Generates final tables, figures, and the figure manifest from compact result files. | No |

The lightweight GitHub repository does not include the large immutable A1/A2 raw-output trees. Consequently, raw-evidence analysis scripts require the separately preserved evidence archive at the documented paths. The committed compact CSV/JSON results and report remain inspectable without that archive.

## Additional deterministic analysis and preparation

| Script | Purpose |
|---|---|
| `analyze_stage_a1.py` | Audits A1 task completeness, calls, parse stages, failures, structural metrics, and telemetry from preserved A1 evidence. |
| `prepare_ontology_dataset.py` | Reconstructs and audits the normalized ontology-generation dataset from authoritative source fixtures. |
| `validate_phase3_prompts.py` | Reconstructs prompts, validates call order and previous-output propagation, and supports deterministic prompt snapshots. |
| `summarize_c3_term_jaccard.py` | Summarizes term-Jaccard observations used by the C3 report. |

## Package support file

| File | Purpose |
|---|---|
| `__init__.py` | Marks `scripts` as an importable Python package. |

Large raw model-response trees are not included in this lightweight repository. Scripts that analyze those artifacts require the separately preserved evidence archive; the submitted compact results and report remain directly inspectable.

## Safe verification commands

These checks use deterministic mocks and make no real model request:

```powershell
python -m pytest -q
python -m compileall restapi scripts tests
```

## Commands that can generate model output

Do not run the following merely to verify a clone:

```text
python -m scripts.run_stage_a1
python -m scripts.run_stage_a2 --execute
python -m scripts.run_stage_a2 --resume
python -m scripts.run_stage_a2 --retry-failed
```

Any new execution requires an exact reviewed model/configuration and a new output namespace. It must not overwrite historical A1/A2 evidence.
