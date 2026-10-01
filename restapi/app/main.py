"""Single FastAPI application for the local benchmark."""

from fastapi import FastAPI

from restapi.app.routers.ontology_benchmark import router

app = FastAPI(title="Bench4KE Ontology Generation",
              description="Ollama generation with unmodified responses and offline C2/C3 analysis")
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
