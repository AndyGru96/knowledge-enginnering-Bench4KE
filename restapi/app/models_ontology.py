"""The supported dataset and benchmark request formats."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restapi.app.config import OLLAMA_MODEL

Variant = Literal["P0", "P1", "P2"]


class TurtleConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_format: Literal["ttl"] = "ttl"


class DatasetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1)
    scenario_id: str | None = None
    scenario: str = Field(min_length=1)
    competency_questions: list[str] = Field(min_length=1)
    user_stories: list[str] | None = None
    constraints: TurtleConstraints | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_content(self):
        if not self.dataset_id.strip() or not self.scenario.strip():
            raise ValueError("dataset_id and scenario must contain text")
        if any(not cq.strip() for cq in self.competency_questions):
            raise ValueError("competency_questions must contain nonempty text")
        return self


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system: Literal["ontogenia", "domain-ontogen", "neon-gpt", "all"] = "all"
    prompt_variants: list[Variant] = Field(default_factory=lambda: ["P0"], min_length=1)
    dataset_path: str | None = None
    items: list[DatasetItem] | None = None
    model: str = Field(default=OLLAMA_MODEL, min_length=1)
    temperature: float = Field(default=0.0, ge=0)
    seed: int | None = 42
    num_ctx: int = Field(default=32768, gt=0)
    max_output_tokens: int = Field(default=8192, gt=0)
    timeout_seconds: float = Field(default=1800, gt=0)
    keep_alive: str = "30m"
    max_items: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_selection(self):
        if len(set(self.prompt_variants)) != len(self.prompt_variants):
            raise ValueError("prompt_variants must not contain duplicates")
        if self.items is not None and self.dataset_path is not None:
            raise ValueError("Provide items or dataset_path, not both")
        if self.items is not None and not self.items:
            raise ValueError("items must not be empty")
        if not self.model.strip():
            raise ValueError("model must contain text")
        return self
