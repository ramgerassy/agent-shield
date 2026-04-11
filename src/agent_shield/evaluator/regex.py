from __future__ import annotations

import re

from agent_shield.config.schema import AssertionResult
from agent_shield.evaluator.base import BaseEvaluator

_FLAG_MAP = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
}


def _parse_flags(flags: str | None) -> int:
    if not flags:
        return 0
    result = 0
    for ch in flags:
        flag = _FLAG_MAP.get(ch.lower())
        if flag is None:
            raise ValueError(f"Unknown regex flag: '{ch}'")
        result |= flag
    return result


class RegexEvaluator(BaseEvaluator):
    """Pass if the response matches the regex pattern."""

    def evaluate(self, response: str) -> AssertionResult:
        pattern = self.assertion.pattern
        try:
            flags = _parse_flags(self.assertion.flags)
            match = re.search(pattern, response, flags)
        except (re.error, ValueError) as e:
            return AssertionResult(
                type="regex",
                expected=pattern,
                passed=False,
                detail=f"Invalid regex: {e}",
            )

        passed = match is not None
        detail = (
            f"Pattern matched: '{match.group(0)[:60]}'"
            if passed
            else "Pattern did not match"
        )

        return AssertionResult(
            type="regex",
            expected=pattern,
            passed=passed,
            detail=detail,
        )
