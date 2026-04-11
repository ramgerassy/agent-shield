from __future__ import annotations

from agent_shield.config.schema import AssertionResult
from agent_shield.evaluator.base import BaseEvaluator


class MinLengthEvaluator(BaseEvaluator):
    """Pass if the response length is >= the configured minimum."""

    def evaluate(self, response: str) -> AssertionResult:
        minimum = int(self.assertion.value)
        actual = len(response)
        passed = actual >= minimum
        detail = (
            f"Response length {actual} >= minimum {minimum}"
            if passed
            else f"Response length {actual} < minimum {minimum}"
        )
        return AssertionResult(
            type="min-length",
            expected=minimum,
            passed=passed,
            detail=detail,
        )


class MaxLengthEvaluator(BaseEvaluator):
    """Pass if the response length is <= the configured maximum."""

    def evaluate(self, response: str) -> AssertionResult:
        maximum = int(self.assertion.value)
        actual = len(response)
        passed = actual <= maximum
        detail = (
            f"Response length {actual} <= maximum {maximum}"
            if passed
            else f"Response length {actual} > maximum {maximum}"
        )
        return AssertionResult(
            type="max-length",
            expected=maximum,
            passed=passed,
            detail=detail,
        )
