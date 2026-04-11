from __future__ import annotations

from agent_shield.config.schema import Assertion
from agent_shield.evaluator.base import BaseEvaluator
from agent_shield.evaluator.contains import ContainsEvaluator, NotContainsEvaluator
from agent_shield.evaluator.json_schema import JsonSchemaEvaluator
from agent_shield.evaluator.length import MaxLengthEvaluator, MinLengthEvaluator
from agent_shield.evaluator.regex import RegexEvaluator

EVALUATOR_REGISTRY: dict[str, type[BaseEvaluator]] = {
    "contains": ContainsEvaluator,
    "not-contains": NotContainsEvaluator,
    "regex": RegexEvaluator,
    "json-schema": JsonSchemaEvaluator,
    "min-length": MinLengthEvaluator,
    "max-length": MaxLengthEvaluator,
}


def get_evaluator(assertion: Assertion) -> BaseEvaluator:
    """Return the evaluator instance for a given assertion."""
    cls = EVALUATOR_REGISTRY.get(assertion.type)
    if cls is None:
        raise ValueError(f"Unknown assertion type: {assertion.type}")
    return cls(assertion)


__all__ = [
    "BaseEvaluator",
    "ContainsEvaluator",
    "NotContainsEvaluator",
    "RegexEvaluator",
    "JsonSchemaEvaluator",
    "MinLengthEvaluator",
    "MaxLengthEvaluator",
    "EVALUATOR_REGISTRY",
    "get_evaluator",
]
