from __future__ import annotations

from abc import ABC, abstractmethod

from agent_shield.config.schema import Assertion, AssertionResult


class BaseEvaluator(ABC):
    """Base class for all assertion evaluators.

    Each subclass implements the strategy for one assertion type. The
    `evaluate` method receives the agent's response text and returns a
    structured AssertionResult.
    """

    def __init__(self, assertion: Assertion):
        self.assertion = assertion

    @abstractmethod
    def evaluate(self, response: str) -> AssertionResult:
        ...
