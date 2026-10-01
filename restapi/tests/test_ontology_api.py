
from fastapi.testclient import TestClient

from restapi.app import main
from restapi.app.routers import ontology_benchmark as api_routes
from test_provider_artifacts import item, client, session


def test_api_all_expands_three_methods_and_rejects_unknown_options(tmp_path, monkeypatch, client, item):
    original = api_routes.run_benchmark
    monkeypatch.setattr(api_routes, "run_benchmark", lambda req, provider: original(req, provider, tmp_path))
    main.app.dependency_overrides[api_routes.get_client] = lambda: client
    try:
        with TestClient(main.app) as api:
            assert api.get("/health").status_code == 200
            response = api.post("/ontology/run", json={"items": [item.model_dump()]})
            assert response.status_code == 200
            assert response.json()["tasks"] == 3
            assert {r["method"] for r in response.json()["results"]} == {"ontogenia", "domain-ontogen", "neon-gpt"}
            assert api.post("/ontology/run", json={"system": "unknown"}).status_code == 422
            assert api.post("/ontology/run", json={"prompt_variants": ["P1", "P1"]}).status_code == 422
            assert api.post("/ontology/run", json={"unknown_option": True}).status_code == 422
            invalid_item = item.model_dump()
            invalid_item["constraints"] = {"output_format": "rdfxml"}
            assert api.post("/ontology/run", json={"items": [invalid_item]}).status_code == 422
    finally:
        main.app.dependency_overrides.clear()

