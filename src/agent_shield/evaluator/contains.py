from __future__ import annotations

from agent_shield.config.schema import AssertionResult
from agent_shield.evaluator.base import BaseEvaluator


def _coerce_values(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


class ContainsEvaluator(BaseEvaluator):
    """Pass if the response contains all/any of the expected values (case-insensitive)."""

    def evaluate(self, response: str) -> AssertionResult:
        values = _coerce_values(self.assertion.value)
        mode = self.assertion.mode
        response_lower = response.lower()

        found = [v for v in values if v.lower() in response_lower]
        missing = [v for v in values if v.lower() not in response_lower]

        if mode == "any":
            passed = len(found) > 0
            detail = (
                f"Found: {found}" if passed else f"None of the expected values found in response"
            )
        else:  # "all"
            passed = len(missing) == 0
            detail = "All values found in response" if passed else f"Missing: {missing}"

        return AssertionResult(
            type="contains",
            expected=values,
            mode=mode,
            passed=passed,
            detail=detail,
        )


class NotContainsEvaluator(BaseEvaluator):
    """Pass if the response contains none of the forbidden values (case-insensitive)."""

    def evaluate(self, response: str) -> AssertionResult:
        values = _coerce_values(self.assertion.value)
        response_lower = response.lower()

        found = [v for v in values if v.lower() in response_lower]
        passed = len(found) == 0
        detail = (
            "None of the forbidden values found in response"
            if passed
            else f"Found forbidden: {found}"
        )

        return AssertionResult(
            type="not-contains",
            expected=values,
            passed=passed,
            detail=detail,
        )
