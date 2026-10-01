"""Project-relative paths and local Ollama configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
DATASET = ROOT / "datasets/ontology_generation/normalized/project2_full_generation.jsonl"
OUTPUTS = ROOT / "outputs"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:30b-a3b-instruct-2507-q4_K_M")


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


METHODS = ("ontogenia", "domain-ontogen", "neon-gpt")
VARIANTS = ("P0", "P1", "P2")
PROCEDURES = {
    "ontogenia": "memoryless_cq_by_cq_with_turtle_comment_protocol",
    "domain-ontogen": "independent_cq_by_cq_with_turtle_comment_protocol",
    "neon-gpt": "scenario_adapted_ten_step_conversation",
}
