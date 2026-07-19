import os
from dotenv import load_dotenv
load_dotenv()

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Ontology benchmark configuration
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OUTPUTS_DIR = os.getenv(
    "OUTPUTS_DIR",
    os.path.join(ROOT_DIR, "restapi", "outputs"),
)
ONTOLOGY_DATASET_DIR = os.getenv(
    "ONTOLOGY_DATASET_DIR",
    os.path.join(ROOT_DIR, "datasets", "ontology_generation", "normalized"),
)
ONTOLOGY_RUNS_DIR = os.getenv(
    "ONTOLOGY_RUNS_DIR",
    os.path.join(OUTPUTS_DIR, "ontology_benchmark", "runs"),
)
ONTOLOGY_PROJECT2_OUTPUT_DIR = os.getenv(
    "ONTOLOGY_PROJECT2_OUTPUT_DIR",
    os.path.join(ROOT_DIR, "outputs", "project2"),
)
EXTERNAL_ONTOLOGY_SERVICE_URL = os.getenv(
    "EXTERNAL_ONTOLOGY_SERVICE_URL",
    "http://127.0.0.1:8020/generate_ontology",
)
ONTOLOGY_EXTERNAL_TIMEOUT = float(os.getenv("ONTOLOGY_EXTERNAL_TIMEOUT", "300"))
OOPS_API_URL = os.getenv("OOPS_API_URL", "")
OOPS_API_MODE = os.getenv("OOPS_API_MODE", "text")  # text|file|url|xml
OOPS_API_TIMEOUT = float(os.getenv("OOPS_API_TIMEOUT", "60"))
ONTOLOGY_LLM_EVAL_PROMPT_PATH = os.getenv(
    "ONTOLOGY_LLM_EVAL_PROMPT_PATH",
    os.path.join(ROOT_DIR, "datasets", "ontology_generation", "prompts", "oe_assist_prompt.txt"),
)
ONTOLOGY_LLM_EVAL_MODEL = os.getenv("ONTOLOGY_LLM_EVAL_MODEL", OPENAI_MODEL)
ONTOLOGY_LLM_EVAL_MAX_TOKENS = int(os.getenv("ONTOLOGY_LLM_EVAL_MAX_TOKENS", "800"))
ONTOLOGY_LLM_EVAL_MAX_CHARS = int(os.getenv("ONTOLOGY_LLM_EVAL_MAX_CHARS", "12000"))
