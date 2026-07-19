# app/main.py
from fastapi import FastAPI

from app.routers import ontology_benchmark

app = FastAPI(
    title="Bench4KE Ontology Generation API",
    description="Local ontology generation and evaluation with the three course methods",
    version="1.0.0"
)

app.include_router(ontology_benchmark.router, prefix="/ontology", tags=["Ontology Benchmark"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
