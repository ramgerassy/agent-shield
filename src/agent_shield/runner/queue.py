from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from agent_shield.config.schema import RateLimitConfig

T = TypeVar("T")


class ConcurrencyQueue:
    """Limits the number of concurrent coroutines via an asyncio Semaphore.

    This bounds parallelism across test *runs* — not steps within a single
    conversation, which always execute sequentially because each step depends
    on the previous response.
    """

    def __init__(self, max_concurrent: int):
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def run(self, coro: Awaitable[T]) -> T:
        """Acquire the semaphore, await the coroutine, release."""
        async with self._semaphore:
            return await coro


class RateLimiter:
    """Token-bucket rate limiter for outbound HTTP requests.

    The bucket has a capacity of `requests` tokens and refills continuously
    at `requests / period_seconds` tokens per second. Each call to
    `acquire()` consumes one token, waiting if the bucket is empty. This
    smooths bursts to a steady rate while still allowing brief catch-up
    bursts up to capacity.

    Industry-standard target unit is requests-per-minute (matches the
    quotas published by OpenAI, Anthropic, Google, etc.). Per-second
    is also supported for fine-grained limits.
    """

    def __init__(self, config: RateLimitConfig):
        self._capacity = float(config.requests)
        period_seconds = 60.0 if config.per == "minute" else 1.0
        self._refill_rate = self._capacity / period_seconds  # tokens per second
        self._tokens = self._capacity
        self._lock = asyncio.Lock()
        # Use the running loop's clock so test mocking is straightforward
        self._last_refill = self._now()

    @staticmethod
    def _now() -> float:
        return asyncio.get_event_loop().time()

    def _refill(self) -> None:
        now = self._now()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._refill_rate
            )
            self._last_refill = now

    async def acquire(self) -> None:
        """Acquire one token, waiting if necessary."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                # Compute exact wait time until the next token is available
                deficit = 1 - self._tokens
                wait_time = deficit / self._refill_rate
            await asyncio.sleep(wait_time)
