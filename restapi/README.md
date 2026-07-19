# Ontology Generation API

This directory contains the ontology-generation implementation used by the course project. It supports the three evaluated methods (`ontogenia`, `domain-ontogen`, and `neon-gpt`), local Ollama execution, result envelopes, parse metadata, telemetry, and cache-aware resume behavior.

## Install

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` only when running a local service. The submitted tests do not require a live model.

## Start the ontology adapter

```powershell
cd restapi
python ontology_adapter.py
```

The adapter exposes `POST /generate_ontology` on port `8020`. Requests identify one of the three methods and include the scenario, ordered competency questions, optional user stories, constraints, and frozen generation metadata.

## Start the benchmark API

In another terminal:

```powershell
cd restapi
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The API exposes `POST /ontology/run`. It can load the normalized JSONL dataset or accept inline items, call the adapter, compute configured ontology metrics, and preserve task artifacts. Generation requires the exact Ollama model recorded in the experiment configuration.

## Core files

- `ontology_adapter.py`: prompt assembly, method pipelines, Ollama/OpenAI-compatible provider selection, normalization, repair, and per-call telemetry.
- `app/routers/ontology_benchmark.py`: dataset execution, cache identity, artifact persistence, and metric orchestration.
- `app/utils/llm_clients.py`: deterministic provider interface and native Ollama client.
- `app/utils/ontology_artifacts.py`: result-envelope, parsing, cache, and resume contracts.
- `app/services/ontology_metrics.py`: ontology structural and documentation-related metrics.

## Safe verification

From the repository root:

```powershell
python -m pytest -q
python -m compileall restapi scripts tests
```

The provider tests use mocks and make no real model request.
