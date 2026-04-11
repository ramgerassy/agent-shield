from __future__ import annotations

import warnings
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- Config models ---


class AgentConfig(BaseModel):
    endpoint: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: dict[str, Any] = Field(
        default_factory=lambda: {"messages": "{{messages}}"}
    )
    response_path: str | None = None

    # Optional user-supplied hooks (dotted import paths: "module.function").
    # Per-test overrides on TestCase win over these defaults.
    custom_request: str | None = None
    custom_extract: str | None = None


class RateLimitConfig(BaseModel):
    """Token-bucket rate limit applied across all requests to the agent."""

    requests: int = Field(..., ge=1, description="Tokens (requests) per period")
    per: Literal["second", "minute"] = "minute"


class SettingsConfig(BaseModel):
    threshold: int = 85
    timeout_ms: int = 30000
    concurrency: int = 3
    retries: int = 1
    output: str = "./agent-shield-report.json"
    rate_limit: RateLimitConfig | None = None

    # Optional additional output formats. Each is opt-in via this path field
    # (or its corresponding CLI flag). The JUnit XML report is auto-enabled
    # in --ci mode if no path is set, defaulting to ./agent-shield-junit.xml.
    junit_output: str | None = None
    html_output: str | None = None
    markdown_output: str | None = None


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


class ConversationStep(BaseModel):
    """A single user turn in a multi-turn conversation."""

    model_config = ConfigDict(populate_by_name=True)

    role: Literal["user"] = "user"
    prompt: str
    assertions: list[Assertion] = Field(default_factory=list, alias="assert")


class TestCase(BaseModel):
    """A test case — either single-turn (prompt + assert) or multi-turn (conversation)."""

    # Tell pytest this is not a test class
    __test__ = False

    model_config = ConfigDict(populate_by_name=True)

    name: str
    repeat: int = 1

    # Single-turn fields
    prompt: str | None = None
    assertions: list[Assertion] | None = Field(default=None, alias="assert")

    # Multi-turn fields
    conversation: list[ConversationStep] | None = None
    on_step_fail: Literal["stop", "continue"] = "stop"

    # Per-test hook overrides (dotted import paths)
    custom_request: str | None = None
    custom_extract: str | None = None

    @model_validator(mode="after")
    def check_test_shape(self) -> TestCase:
        has_single = self.prompt is not None
        has_multi = self.conversation is not None

        if has_single and has_multi:
            raise ValueError(
                f"Test '{self.name}' cannot have both 'prompt' and 'conversation'"
            )
        if not has_single and not has_multi:
            raise ValueError(
                f"Test '{self.name}' must have either 'prompt' or 'conversation'"
            )
        if has_single and self.assertions is None:
            raise ValueError(
                f"Test '{self.name}' has 'prompt' but is missing 'assert'"
            )

        if self.repeat < 1:
            raise ValueError(
                f"Test '{self.name}' has invalid repeat={self.repeat}; must be >= 1"
            )
        if self.repeat > 100:
            warnings.warn(
                f"Test '{self.name}' has repeat={self.repeat} (>100); "
                f"this may incur high token costs.",
                UserWarning,
                stacklevel=2,
            )

        return self

    @property
    def is_multi_turn(self) -> bool:
        return self.conversation is not None

    @property
    def steps(self) -> list[ConversationStep]:
        """Return the test as a list of conversation steps.

        Single-turn tests are normalized to a single-step conversation so the
        executor can treat all tests uniformly.
        """
        if self.conversation is not None:
            return self.conversation
        # Single-turn → wrap in a single ConversationStep
        return [
            ConversationStep(
                role="user",
                prompt=self.prompt,  # type: ignore[arg-type]
                assertions=self.assertions or [],
            )
        ]


class ShieldConfig(BaseModel):
    agent: AgentConfig
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    tests: list[TestCase]


# --- Result models ---


class AssertionResult(BaseModel):
    type: str
    expected: Any = None
    mode: str | None = None
    passed: bool
    detail: str


class StepResult(BaseModel):
    """Result of a single conversation step."""

    step: int
    prompt: str
    response: str = ""
    status: Literal["passed", "failed", "skipped"]
    score: float
    assertions: list[AssertionResult] = Field(default_factory=list)


class RunResult(BaseModel):
    """Result of one execution of a test (one of N repeat runs)."""

    run: int
    passed: bool
    score: float
    duration_ms: int

    # Multi-turn (or normalized single-turn): step-by-step results
    steps: list[StepResult] = Field(default_factory=list)


class TestResult(BaseModel):
    """Aggregated result for a test case across all repeat runs."""

    # Tell pytest this is not a test class
    __test__ = False

    name: str
    type: Literal["single-turn", "multi-turn"]
    repeat: int
    pass_rate: float
    consistency: float
    score: float
    passed: bool
    on_step_fail: str | None = None
    runs: list[RunResult]
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
