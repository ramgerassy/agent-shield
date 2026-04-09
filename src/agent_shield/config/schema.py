from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- Config models ---


class AgentConfig(BaseModel):
    endpoint: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: dict[str, Any] = Field(
        default_factory=lambda: {"message": "{{prompt}}"}
    )
    response_path: str | None = None


class SettingsConfig(BaseModel):
    threshold: int = 85
    timeout_ms: int = 30000
    concurrency: int = 3
    retries: int = 1
    output: str = "./agent-shield-report.json"


AssertionType = Literal[
    "contains",
    "not-contains",
    "regex",
    "json-schema",
    "min-length",
    "max-length",
]


class Assertion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: AssertionType
    value: Union[str, int, float, list[str], None] = None
    pattern: str | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    flags: str | None = None
    mode: Literal["all", "any"] = "all"

    @model_validator(mode="after")
    def check_required_fields(self) -> Assertion:
        t = self.type
        if t in ("contains", "not-contains") and self.value is None:
            raise ValueError(f"Assertion type '{t}' requires 'value'")
        if t == "regex" and self.pattern is None:
            raise ValueError("Assertion type 'regex' requires 'pattern'")
        if t == "json-schema" and self.schema_ is None:
            raise ValueError("Assertion type 'json-schema' requires 'schema'")
        if t in ("min-length", "max-length") and self.value is None:
            raise ValueError(f"Assertion type '{t}' requires 'value'")
        return self


class TestCase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    prompt: str
    assertions: list[Assertion] = Field(alias="assert")


class ShieldConfig(BaseModel):
    agent: AgentConfig
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    tests: list[TestCase]


# --- Result models ---


class AssertionResult(BaseModel):
    type: str
    expected: Any = None
    passed: bool
    detail: str


class TestResult(BaseModel):
    name: str
    score: float
    passed: bool
    prompt: str
    response: str
    assertions: list[AssertionResult]
    duration_ms: int


class RunReport(BaseModel):
    run_id: str
    timestamp: str
    agent_endpoint: str
    overall_score: float
    threshold: int
    passed: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    duration_ms: int
    results: list[TestResult]
