from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import jmespath

from agent_shield.config.schema import (
    AgentConfig,
    AssertionResult,
    RunResult,
    SettingsConfig,
    StepResult,
    TestCase,
    TestResult,
)
from agent_shield.evaluator import get_evaluator
from agent_shield.runner.hooks import (
    CustomExtractFn,
    CustomRequestFn,
    resolve_extract_hook,
    resolve_request_hook,
)
from agent_shield.runner.queue import ConcurrencyQueue, RateLimiter


class TestExecutor:
    """Executes test cases against an agent endpoint.

    For each test case, runs `test.repeat` independent runs through a shared
    concurrency-limited queue. Each run executes its conversation steps
    sequentially, accumulating message history as it goes. Single-turn tests
    are normalized to a single-step conversation by `TestCase.steps`.

    Custom request and extract hooks can be supplied at the agent level
    (default for all tests) or per-test (overrides the default). Tests with
    no hooks fall back to the built-in httpx + jmespath implementation.
    """

    # Tell pytest this is not a test class
    __test__ = False

    def __init__(self, agent_config: AgentConfig, settings: SettingsConfig):
        self.agent_config = agent_config
        self.settings = settings
        # Created per run_all() call so each top-level invocation has a fresh bucket
        self._rate_limiter: RateLimiter | None = None

    async def run_all(self, tests: list[TestCase]) -> list[TestResult]:
        """Run all tests and return aggregated results."""
        timeout = httpx.Timeout(self.settings.timeout_ms / 1000.0)
        self._rate_limiter = (
            RateLimiter(self.settings.rate_limit)
            if self.settings.rate_limit is not None
            else None
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            queue = ConcurrencyQueue(self.settings.concurrency)
            test_coros = [self._execute_test(t, client, queue) for t in tests]
            return await asyncio.gather(*test_coros)

    async def _execute_test(
        self,
        test: TestCase,
        client: httpx.AsyncClient,
        queue: ConcurrencyQueue,
    ) -> TestResult:
        """Schedule N=repeat runs through the queue and aggregate the results."""
        # Resolve hooks once per test (per-test override > agent-level > builtin)
        request_hook = self._resolve_request_hook(test)
        extract_hook = self._resolve_extract_hook(test)

        run_coros = [
            queue.run(
                self._execute_run(test, run_idx + 1, client, request_hook, extract_hook)
            )
            for run_idx in range(test.repeat)
        ]
        runs = await asyncio.gather(*run_coros)
        return self._aggregate_runs(test, runs)

    def _resolve_request_hook(self, test: TestCase) -> CustomRequestFn | None:
        path = test.custom_request or self.agent_config.custom_request
        return resolve_request_hook(path) if path else None

    def _resolve_extract_hook(self, test: TestCase) -> CustomExtractFn | None:
        path = test.custom_extract or self.agent_config.custom_extract
        return resolve_extract_hook(path) if path else None

    async def _execute_run(
        self,
        test: TestCase,
        run_id: int,
        client: httpx.AsyncClient,
        request_hook: CustomRequestFn | None,
        extract_hook: CustomExtractFn | None,
    ) -> RunResult:
        """Execute one run of a test (one conversation pass)."""
        start = time.monotonic()
        context: list[dict[str, str]] = []
        step_results: list[StepResult] = []
        stopped = False

        for step_idx, step in enumerate(test.steps, start=1):
            if stopped:
                step_results.append(
                    StepResult(
                        step=step_idx,
                        prompt=step.prompt,
                        response="",
                        status="skipped",
                        score=0.0,
                        assertions=[],
                    )
                )
                continue

            # Add the user message to the conversation context
            context.append({"role": "user", "content": step.prompt})

            # Build the request body using the template
            body = self._build_body(
                self.agent_config.body_template, context, step.prompt
            )

            # Send the request, with retries
            try:
                response_text = await self._send_request(
                    client, body, request_hook, extract_hook
                )
            except Exception as e:
                step_results.append(
                    StepResult(
                        step=step_idx,
                        prompt=step.prompt,
                        response=f"ERROR: {e}",
                        status="failed",
                        score=0.0,
                        assertions=[
                            AssertionResult(
                                type="http",
                                expected=None,
                                passed=False,
                                detail=f"Request failed: {e}",
                            )
                        ],
                    )
                )
                if test.on_step_fail == "stop":
                    stopped = True
                continue

            # Append the assistant response to the conversation context
            context.append({"role": "assistant", "content": response_text})

            # Run assertions
            assertion_results = [
                get_evaluator(a).evaluate(response_text) for a in step.assertions
            ]

            # Score the step
            if not assertion_results:
                step_score = 100.0
                step_passed = True
            else:
                passed_count = sum(1 for r in assertion_results if r.passed)
                step_score = passed_count / len(assertion_results) * 100
                step_passed = passed_count == len(assertion_results)

            step_results.append(
                StepResult(
                    step=step_idx,
                    prompt=step.prompt,
                    response=response_text,
                    status="passed" if step_passed else "failed",
                    score=step_score,
                    assertions=assertion_results,
                )
            )

            if not step_passed and test.on_step_fail == "stop":
                stopped = True

        duration_ms = int((time.monotonic() - start) * 1000)
        run_score = (
            sum(s.score for s in step_results) / len(step_results)
            if step_results
            else 0.0
        )
        run_passed = all(s.status == "passed" for s in step_results)

        return RunResult(
            run=run_id,
            passed=run_passed,
            score=run_score,
            duration_ms=duration_ms,
            steps=step_results,
        )

    async def _send_request(
        self,
        client: httpx.AsyncClient,
        body: Any,
        request_hook: CustomRequestFn | None,
        extract_hook: CustomExtractFn | None,
    ) -> str:
        """Send the request with retry logic. Returns the extracted response text.

        If `request_hook` is provided, it is used to send the request.
        Otherwise the built-in httpx call is used.
        If `extract_hook` is provided, it is used to extract the response
        text. Otherwise jmespath/response_path or raw text is used.
        """
        last_error: Exception | None = None
        attempts = self.settings.retries + 1

        for _ in range(attempts):
            try:
                if self._rate_limiter is not None:
                    await self._rate_limiter.acquire()
                if request_hook is not None:
                    response = await request_hook(client, self.agent_config, body)
                else:
                    response = await self._default_request(client, body)
                response.raise_for_status()

                if extract_hook is not None:
                    return extract_hook(response)
                return self._default_extract(response)
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                last_error = e

        assert last_error is not None
        raise last_error

    async def _default_request(
        self, client: httpx.AsyncClient, body: Any
    ) -> httpx.Response:
        """Built-in request: simple httpx call with the configured method/headers."""
        return await client.request(
            self.agent_config.method,
            self.agent_config.endpoint,
            json=body,
            headers=self.agent_config.headers,
        )

    def _default_extract(self, response: httpx.Response) -> str:
        """Built-in extraction: jmespath response_path if set, otherwise raw text."""
        if self.agent_config.response_path:
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                raise ValueError(f"Response is not valid JSON: {e.msg}") from e
            extracted = jmespath.search(self.agent_config.response_path, data)
            if extracted is None:
                raise ValueError(
                    f"jmespath '{self.agent_config.response_path}' returned None"
                )
            return str(extracted)
        return response.text

    def _build_body(
        self,
        template: Any,
        context: list[dict[str, str]],
        current_prompt: str,
    ) -> Any:
        """Recursively replace placeholders in the body template.

        - `"{{messages}}"` (entire string value) → replaced with the context list
          (a JSON array in the serialized body)
        - `{{prompt}}` (substring) → replaced with the current step's prompt
        - `{{history}}` (substring) → replaced with the formatted conversation
        """
        history_str = self._format_history(context)

        def walk(value: Any) -> Any:
            if isinstance(value, str):
                # Special case: exactly "{{messages}}" → return the list itself
                if value == "{{messages}}":
                    return context
                return value.replace("{{prompt}}", current_prompt).replace(
                    "{{history}}", history_str
                )
            if isinstance(value, dict):
                return {k: walk(v) for k, v in value.items()}
            if isinstance(value, list):
                return [walk(v) for v in value]
            return value

        return walk(template)

    @staticmethod
    def _format_history(context: list[dict[str, str]]) -> str:
        return "\n".join(f"{m['role']}: {m['content']}" for m in context)

    @staticmethod
    def _aggregate_runs(test: TestCase, runs: list[RunResult]) -> TestResult:
        """Aggregate N repeat runs into a TestResult with pass_rate and consistency."""
        total = len(runs)
        passed_runs = sum(1 for r in runs if r.passed)
        failed_runs = total - passed_runs

        pass_rate = passed_runs / total * 100 if total else 0.0
        # Consistency = % of runs whose pass/fail outcome matches the majority
        majority = max(passed_runs, failed_runs)
        consistency = majority / total * 100 if total else 0.0

        return TestResult(
            name=test.name,
            type="multi-turn" if test.is_multi_turn else "single-turn",
            repeat=test.repeat,
            pass_rate=pass_rate,
            consistency=consistency,
            score=pass_rate,
            passed=pass_rate == 100,
            on_step_fail=test.on_step_fail if test.is_multi_turn else None,
            runs=runs,
            duration_ms=sum(r.duration_ms for r in runs),
        )
