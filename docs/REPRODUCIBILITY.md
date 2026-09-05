# Reproducing the Project

## Environment

The project was tested with Python 3.13. Install the dependencies from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the tests with:

```powershell
python -m pytest -q
python -m compileall restapi scripts tests
```

The current repository contains 47 passing tests. Tests use local recording objects and do not contact an Ollama server.

## Data and analysis

The normalized dataset and prompt files are included under `datasets/ontology_generation/`. Final tables are under `results/`, and the report figures are under `report/figures/`.

The complete A1 and A2 generation trees are stored separately because they contain 6,906 files and about 124 MB of responses and intermediate outputs. The repository includes one complete A2 P1 example under `evidence/` and the task-level A2 verification table under `results/`.

The reported generation used:

- Ollama 0.32.0;
- `qwen3:30b-a3b-instruct-2507-q4_K_M`;
- temperature 0 and seed 42;
- `num_ctx=32768` and `num_predict=8192`;
- `keep_alive=30m`;
- non-streaming responses.

New generation is performed through the API and ontology adapter. The existing CSV results can be inspected without running a model.

## Interpretation

The experiment compares instruction-level suffixes rather than unrestricted prompt rephrasing. Parseability does not establish conceptual correctness, CQ coverage, logical consistency, or practical usefulness. Documentation measurements are conditional on parseable output, and some prompt-sensitivity comparisons have small paired samples.
