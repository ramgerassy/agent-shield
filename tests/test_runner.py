from __future__ import annotations

import asyncio
import json
import sys
import types

import httpx
import pytest
import respx

from agent_shield.config.schema import (
    AgentConfig,
    Assertion,
    ConversationStep,
    RateLimitConfig,
    SettingsConfig,
    TestCase,
)
from agent_shield.runner.executor import TestExecutor
from agent_shield.runner.queue import ConcurrencyQueue, RateLimiter


def make_agent(**overrides) -> AgentConfig:
    base = {
        "endpoint": "https://agent.test/api/chat",
        "method": "POST",
        "headers": {"Authorization": "Bearer test"},
        "body_template": {"messages": "{{messages}}"},
    }
    base.update(overrides)
    return AgentConfig(**base)


def make_settings(**overrides) -> SettingsConfig:
    base = {"threshold": 85, "timeout_ms": 5000, "concurrency": 3, "retries": 0}
    base.update(overrides)
    return SettingsConfig(**base)


# --- ConcurrencyQueue ---


class TestConcurrencyQueue:
    async def test_limits_parallelism(self):
        queue = ConcurrencyQueue(2)
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def task():
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1

        await asyncio.gather(*(queue.run(task()) for _ in range(6)))
        assert peak <= 2

    def test_invalid_concurrency_raises(self):
        with pytest.raises(ValueError):
            ConcurrencyQueue(0)


# --- Single-turn execution ---


class TestSingleTurn:
    @respx.mock
    async def test_basic_pass(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(200, text="Hello world response")
        )
        test = TestCase(
            name="basic",
            prompt="say hi",
            assertions=[Assertion(type="contains", value=["hello"])],
        )
        executor = TestExecutor(make_agent(), make_settings())
        results = await executor.run_all([test])

        assert len(results) == 1
        result = results[0]
        assert result.type == "single-turn"
        assert result.passed
        assert result.pass_rate == 100
        assert result.consistency == 100
        assert len(result.runs) == 1
        assert result.runs[0].passed
        assert len(result.runs[0].steps) == 1

    @respx.mock
    async def test_assertion_failure(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(200, text="goodbye")
        )
        test = TestCase(
            name="fail",
            prompt="say hi",
            assertions=[Assertion(type="contains", value=["hello"])],
        )
        executor = TestExecutor(make_agent(), make_settings())
        results = await executor.run_all([test])
        assert not results[0].passed
        assert results[0].pass_rate == 0

    @respx.mock
    async def test_response_path_extraction(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "extracted text"}}]},
            )
        )
        agent = make_agent(response_path="choices[0].message.content")
        test = TestCase(
            name="extract",
            prompt="hi",
            assertions=[Assertion(type="contains", value=["extracted"])],
        )
        executor = TestExecutor(agent, make_settings())
        results = await executor.run_all([test])
        assert results[0].passed
        assert "extracted text" in results[0].runs[0].steps[0].response

    @respx.mock
    async def test_prompt_substitution_in_body(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, text="ok")

        respx.post("https://agent.test/api/chat").mock(side_effect=handler)
        agent = make_agent(body_template={"prompt": "{{prompt}}"})
        test = TestCase(
            name="prompt-sub",
            prompt="hello there",
            assertions=[Assertion(type="contains", value=["ok"])],
        )
        await TestExecutor(agent, make_settings()).run_all([test])
        assert captured["body"] == {"prompt": "hello there"}

    @respx.mock
    async def test_messages_placeholder_becomes_list(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, text="ok")

        respx.post("https://agent.test/api/chat").mock(side_effect=handler)
        test = TestCase(
            name="msgs",
            prompt="hi",
            assertions=[Assertion(type="contains", value=["ok"])],
        )
        await TestExecutor(make_agent(), make_settings()).run_all([test])
        assert captured["body"] == {"messages": [{"role": "user", "content": "hi"}]}


# --- Multi-turn conversations ---


class TestMultiTurn:
    @respx.mock
    async def test_message_history_accumulates(self):
        captured_bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            turn = len(captured_bodies)
            return httpx.Response(200, text=f"response {turn}")

        respx.post("https://agent.test/api/chat").mock(side_effect=handler)
        test = TestCase(
            name="multi",
            conversation=[
                ConversationStep(
                    prompt="first",
                    assertions=[Assertion(type="contains", value=["response 1"])],
                ),
                ConversationStep(
                    prompt="second",
                    assertions=[Assertion(type="contains", value=["response 2"])],
                ),
            ],
        )
        results = await TestExecutor(make_agent(), make_settings()).run_all([test])

        assert results[0].type == "multi-turn"
        assert results[0].passed
        assert len(captured_bodies) == 2
        # Turn 1: just the first user message
        assert captured_bodies[0]["messages"] == [
            {"role": "user", "content": "first"}
        ]
        # Turn 2: full history including assistant turn
        assert captured_bodies[1]["messages"] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response 1"},
            {"role": "user", "content": "second"},
        ]

    @respx.mock
    async def test_on_step_fail_stop(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(200, text="nope")
        )
        test = TestCase(
            name="stop-on-fail",
            on_step_fail="stop",
            conversation=[
                ConversationStep(
                    prompt="step1",
                    assertions=[Assertion(type="contains", value=["yes"])],
                ),
                ConversationStep(
                    prompt="step2",
                    assertions=[Assertion(type="contains", value=["yes"])],
                ),
                ConversationStep(
                    prompt="step3",
                    assertions=[Assertion(type="contains", value=["yes"])],
                ),
            ],
        )
        results = await TestExecutor(make_agent(), make_settings()).run_all([test])
        steps = results[0].runs[0].steps
        assert steps[0].status == "failed"
        assert steps[1].status == "skipped"
        assert steps[2].status == "skipped"

    @respx.mock
    async def test_on_step_fail_continue(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(200, text="nope")
        )
        test = TestCase(
            name="continue-on-fail",
            on_step_fail="continue",
            conversation=[
                ConversationStep(
                    prompt="step1",
                    assertions=[Assertion(type="contains", value=["yes"])],
                ),
                ConversationStep(
                    prompt="step2",
                    assertions=[Assertion(type="contains", value=["yes"])],
                ),
            ],
        )
        results = await TestExecutor(make_agent(), make_settings()).run_all([test])
        steps = results[0].runs[0].steps
        assert steps[0].status == "failed"
        assert steps[1].status == "failed"  # not skipped


# --- Repeat runs and consistency ---


class TestRepeat:
    @respx.mock
    async def test_all_runs_pass(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(200, text="hello world")
        )
        test = TestCase(
            name="repeat",
            prompt="hi",
            repeat=5,
            assertions=[Assertion(type="contains", value=["hello"])],
        )
        results = await TestExecutor(make_agent(), make_settings()).run_all([test])
        assert results[0].pass_rate == 100
        assert results[0].consistency == 100
        assert len(results[0].runs) == 5

    @respx.mock
    async def test_mixed_runs_consistency(self):
        # Alternate responses: 3 pass, 2 fail
        responses = ["hello", "nope", "hello", "nope", "hello"]
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            r = httpx.Response(200, text=responses[call_count])
            call_count += 1
            return r

        respx.post("https://agent.test/api/chat").mock(side_effect=handler)
        test = TestCase(
            name="flaky",
            prompt="hi",
            repeat=5,
            assertions=[Assertion(type="contains", value=["hello"])],
        )
        # concurrency=1 keeps order deterministic
        results = await TestExecutor(
            make_agent(), make_settings(concurrency=1)
        ).run_all([test])

        assert results[0].pass_rate == 60  # 3/5
        # Majority is "passed" (3/5), consistency = 3/5 = 60
        assert results[0].consistency == 60
        assert not results[0].passed  # pass_rate < 100


# --- Retries and HTTP errors ---


class TestRetriesAndErrors:
    @respx.mock
    async def test_retry_then_succeed(self):
        responses = [
            httpx.Response(500, text="server error"),
            httpx.Response(200, text="hello"),
        ]
        respx.post("https://agent.test/api/chat").mock(side_effect=responses)
        test = TestCase(
            name="retry",
            prompt="hi",
            assertions=[Assertion(type="contains", value=["hello"])],
        )
        results = await TestExecutor(
            make_agent(), make_settings(retries=1)
        ).run_all([test])
        assert results[0].passed

    @respx.mock
    async def test_retries_exhausted_marks_failed(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(500, text="boom")
        )
        test = TestCase(
            name="failretry",
            prompt="hi",
            assertions=[Assertion(type="contains", value=["hello"])],
        )
        results = await TestExecutor(
            make_agent(), make_settings(retries=1)
        ).run_all([test])
        assert not results[0].passed
        step = results[0].runs[0].steps[0]
        assert step.status == "failed"
        assert "ERROR" in step.response


# --- Custom hooks ---


def _install_fake_hook_module():
    """Install a fake module with custom request/extract hooks for testing."""
    mod = types.ModuleType("agent_shield_test_hooks")

    async def custom_request(client, agent_config, body):
        # Verify the executor passes us the templated body
        assert "messages" in body
        # Attach a request so raise_for_status() can be called
        request = httpx.Request(agent_config.method, agent_config.endpoint)
        return httpx.Response(
            200,
            text=json.dumps({"wrapper": {"text": "from custom hook"}}),
            request=request,
        )

    def custom_extract(response):
        return response.json()["wrapper"]["text"]

    async def failing_request(client, agent_config, body):
        raise httpx.HTTPError("simulated transport failure")

    mod.custom_request = custom_request
    mod.custom_extract = custom_extract
    mod.failing_request = failing_request
    sys.modules["agent_shield_test_hooks"] = mod
    return mod


class TestCustomHooks:
    def setup_method(self):
        # Clear the hook resolver cache between tests
        from agent_shield.runner.hooks import resolve_hook

        resolve_hook.cache_clear()
        _install_fake_hook_module()

    async def test_agent_level_request_and_extract_hooks(self):
        agent = make_agent(
            custom_request="agent_shield_test_hooks.custom_request",
            custom_extract="agent_shield_test_hooks.custom_extract",
        )
        test = TestCase(
            name="hooked",
            prompt="hi",
            assertions=[Assertion(type="contains", value=["from custom"])],
        )
        results = await TestExecutor(agent, make_settings()).run_all([test])
        assert results[0].passed
        assert results[0].runs[0].steps[0].response == "from custom hook"

    async def test_per_test_override_wins(self):
        agent = make_agent(
            custom_request="agent_shield_test_hooks.failing_request",
        )
        # Per-test override uses the working hook instead
        test = TestCase(
            name="override",
            prompt="hi",
            custom_request="agent_shield_test_hooks.custom_request",
            custom_extract="agent_shield_test_hooks.custom_extract",
            assertions=[Assertion(type="contains", value=["from custom"])],
        )
        results = await TestExecutor(agent, make_settings()).run_all([test])
        assert results[0].passed

    @respx.mock
    async def test_extract_hook_with_default_request(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(
                200, json={"wrapper": {"text": "from custom hook"}}
            )
        )
        agent = make_agent(
            custom_extract="agent_shield_test_hooks.custom_extract"
        )
        test = TestCase(
            name="extract-only",
            prompt="hi",
            assertions=[Assertion(type="contains", value=["from custom"])],
        )
        results = await TestExecutor(agent, make_settings()).run_all([test])
        assert results[0].passed

    def test_invalid_hook_path_raises_at_resolve(self):
        from agent_shield.runner.hooks import resolve_hook

        with pytest.raises(ValueError, match="must be 'module.function'"):
            resolve_hook("noseparator")

    def test_missing_module_raises(self):
        from agent_shield.runner.hooks import resolve_hook

        with pytest.raises(ValueError, match="Cannot import module"):
            resolve_hook("definitely_not_a_module.func")

    def test_missing_attribute_raises(self):
        from agent_shield.runner.hooks import resolve_hook

        with pytest.raises(ValueError, match="has no attribute"):
            resolve_hook("agent_shield_test_hooks.does_not_exist")

    def test_sync_function_rejected_for_request_hook(self):
        from agent_shield.runner.hooks import resolve_request_hook

        with pytest.raises(ValueError, match="must be an async function"):
            resolve_request_hook("agent_shield_test_hooks.custom_extract")


# --- Rate limiter ---


class TestRateLimiter:
    async def test_initial_burst_up_to_capacity(self):
        # Capacity 5 → first 5 acquisitions should be effectively instantaneous
        limiter = RateLimiter(RateLimitConfig(requests=5, per="second"))
        loop = asyncio.get_event_loop()
        start = loop.time()
        for _ in range(5):
            await limiter.acquire()
        elapsed = loop.time() - start
        assert elapsed < 0.05, f"burst took {elapsed}s, expected near-zero"

    async def test_paces_after_burst(self):
        # 10 tokens/sec → after the initial 10-token burst, each new token
        # should arrive every 0.1s
        limiter = RateLimiter(RateLimitConfig(requests=10, per="second"))
        # Drain the initial bucket
        for _ in range(10):
            await limiter.acquire()
        # The next 5 should take ~0.5s total (~0.1s each)
        loop = asyncio.get_event_loop()
        start = loop.time()
        for _ in range(5):
            await limiter.acquire()
        elapsed = loop.time() - start
        assert 0.4 < elapsed < 0.7, f"expected ~0.5s, got {elapsed}s"

    async def test_minute_unit_converts_correctly(self):
        # 60/minute = 1/sec; bucket capacity = 60
        limiter = RateLimiter(RateLimitConfig(requests=60, per="minute"))
        # Internal refill_rate should be 1.0 token/sec
        assert limiter._refill_rate == pytest.approx(1.0)
        assert limiter._capacity == 60.0


class TestExecutorRateLimit:
    @respx.mock
    async def test_executor_respects_rate_limit(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(200, text="hello")
        )
        # 5/sec means 6 requests should take at least ~0.2s
        # (5 instant from initial bucket, 1 must wait 0.2s)
        settings = make_settings(
            concurrency=10,
            rate_limit=RateLimitConfig(requests=5, per="second"),
        )
        test = TestCase(
            name="rate-limited",
            prompt="hi",
            repeat=6,
            assertions=[Assertion(type="contains", value=["hello"])],
        )
        loop = asyncio.get_event_loop()
        start = loop.time()
        results = await TestExecutor(make_agent(), settings).run_all([test])
        elapsed = loop.time() - start
        assert results[0].passed
        assert elapsed >= 0.18, f"expected >= 0.2s with 6 reqs at 5/sec, got {elapsed}s"

    @respx.mock
    async def test_no_rate_limit_no_wait(self):
        respx.post("https://agent.test/api/chat").mock(
            return_value=httpx.Response(200, text="hello")
        )
        # No rate_limit configured → existing behavior, no waiting
        test = TestCase(
            name="unlimited",
            prompt="hi",
            repeat=10,
            assertions=[Assertion(type="contains", value=["hello"])],
        )
        loop = asyncio.get_event_loop()
        start = loop.time()
        results = await TestExecutor(make_agent(), make_settings()).run_all([test])
        elapsed = loop.time() - start
        assert results[0].passed
        # 10 mocked requests should be near-instant (no I/O)
        assert elapsed < 0.5
