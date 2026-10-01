# Ontology generation API

Install the root requirements.txt, then start the single API from the repository root:

    python -m uvicorn restapi.app.main:app --host 127.0.0.1 --port 8000

GET /health reports API readiness. POST /ontology/run accepts the BenchmarkRequest schema shown at /docs. Ollama must be running separately with the selected model installed.

For command-line generation, run :

    python -m restapi.ontology_adapter --config config/course_methods.yaml

ontology_adapter.py contains generation and prompt assembly. app/routers/ontology_benchmark.py provides the API and checks saved run evidence. app/services/ontology_metrics.py computes C2 documentation measures. app/utils/llm_clients.py talks to Ollama; app/utils/ontology_artifacts.py saves evidence atomically and parses documents with independent blank nodes.

There is one API on port 8000. Responses are saved unchanged and invalid Turtle is recorded as a failure. Dataset and run paths resolve from the repository root.

Offline verification:

    python -m pytest -q
    python -m scripts.validate_prompts

The tests use simulated model responses. Saved report/, results/ and evidence/ files are retained without regeneration.
